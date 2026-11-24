"""Arr service — business logic for Radarr/Sonarr/Seerr operations.

Extracted from routers/settings.py to separate HTTP concerns from domain logic.
"""
import json
import logging
from urllib.parse import urljoin, urlparse

import httpx

from ..db.settings_store import get_setting, set_setting
from ..db.utils import TIMEOUT_SHORT, is_loopback_or_link_local

logger = logging.getLogger(__name__)

_MASK = "***"
_SEERR_MAX_REDIRECTS = 3


async def _reject_if_loopback_or_link_local(url: str) -> str:
    """Return an error message if url's host is loopback/link-local, else ''.

    Guards admin "test connection" endpoints: unlike the image proxy these
    URLs aren't whitelisted (testing a not-yet-saved server is the point), so
    an authenticated session could otherwise use the server as an SSRF oracle
    against itself or the cloud metadata endpoint (169.254.169.254). RFC1918
    LAN addresses stay allowed — real Radarr/Sonarr/Emby live there.
    """
    host = (urlparse(url).hostname or "").lower()
    if host and await is_loopback_or_link_local(host):
        return f"Hôte non autorisé : {host}"
    return ""


async def _ssrf_guard_hook(request: httpx.Request) -> None:
    """httpx request event hook — re-checked on every hop, including redirects.

    A single pre-flight check on the original URL is not enough for clients
    with follow_redirects=True: the already-validated host could redirect the
    request to an internal target, and httpx would follow it without telling
    the caller. Raising here aborts the whole request, redirect or not.
    """
    blocked = await _reject_if_loopback_or_link_local(str(request.url))
    if blocked:
        raise httpx.HTTPError(f"SSRF guard: {blocked}")


async def test_arr_instance(type_: str, url: str, api_key: str) -> dict:
    """Test connectivity to a specific Radarr or Sonarr instance.

    Returns {"ok": bool, "message": str}.
    If api_key is masked, looks up the real key from stored servers.
    """
    url = (url or "").rstrip("/")
    key = api_key or ""

    # If frontend sent the masked value, retrieve real key from DB
    if key == _MASK or not key:
        arr_setting = "radarr_servers" if type_ == "radarr" else "sonarr_servers"
        try:
            servers = json.loads(await get_setting(arr_setting) or "[]")
            for s in servers:
                if s.get("url", "").rstrip("/") == url:
                    key = s.get("api_key", "")
                    break
        except Exception as e:
            logger.debug("arr_service: could not resolve masked API key for %s: %s", url, e)
        if key == _MASK:
            key = ""  # never send the UI placeholder as a real X-Api-Key

    if not url or not key:
        return {"ok": False, "message": "URL et clé API requis"}

    blocked = await _reject_if_loopback_or_link_local(url)
    if blocked:
        return {"ok": False, "message": blocked}

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as c:
            r = await c.get(f"{url}/api/v3/system/status", headers={"X-Api-Key": key})
            if r.status_code == 200:
                version = r.json().get("version", "?")
                label = "Radarr" if type_ == "radarr" else "Sonarr"
                return {"ok": True, "message": f"{label} {version}"}
            return {"ok": False, "message": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def _build_arr_url(srv: dict) -> str:
    """Build a URL string from Seerr service config dict."""
    scheme = "https" if srv.get("useSsl") else "http"
    host   = srv.get("hostname") or ""
    port   = srv.get("port") or (443 if srv.get("useSsl") else 80)
    burl   = (srv.get("baseUrl") or "").strip("/")
    return f"{scheme}://{host}:{port}/{burl}".rstrip("/")


def _normalize_base_url(url: str) -> tuple:
    """(scheme, host, port, path) of a service URL, for same-target comparison."""
    try:
        p = urlparse((url or "").strip())
        scheme = p.scheme.lower()
        port = p.port or (443 if scheme == "https" else 80)
        return (scheme, (p.hostname or "").lower(), port, p.path.rstrip("/"))
    except ValueError:
        return ("", "", 0, "")


async def _seerr_get(client: httpx.AsyncClient, url: str, headers: dict) -> httpx.Response:
    """GET with manual redirect handling so X-Api-Key never leaves the Seerr host.

    httpx only strips Authorization on a cross-origin redirect, not X-Api-Key,
    so redirects are followed here: same hostname only (http -> https allowed,
    https -> http refused), each hop re-validated against the SSRF guard.
    """
    origin_host = (urlparse(url).hostname or "").lower()
    for _ in range(_SEERR_MAX_REDIRECTS + 1):
        resp = await client.get(url, headers=headers)
        location = resp.headers.get("location")
        if resp.status_code not in (301, 302, 303, 307, 308) or not location:
            return resp
        target = urljoin(url, location)
        parsed = urlparse(target)
        if (parsed.hostname or "").lower() != origin_host:
            raise ValueError(
                "Seerr redirige vers un autre hôte : la clé API n'est pas envoyée. "
                "Saisissez l'URL finale de Seerr."
            )
        if parsed.scheme.lower() == "http" and urlparse(url).scheme.lower() == "https":
            raise ValueError("Seerr redirige de HTTPS vers HTTP : redirection refusée")
        if parsed.scheme.lower() not in ("http", "https"):
            raise ValueError("Redirection Seerr vers un schéma non autorisé")
        blocked = await _reject_if_loopback_or_link_local(target)
        if blocked:
            raise ValueError(blocked)
        url = target
    raise ValueError("Trop de redirections depuis Seerr")


def _mask_servers(servers: list[dict]) -> list[dict]:
    """Copies of the server dicts with api_key masked (stored data untouched)."""
    return [{**s, "api_key": _MASK} if s.get("api_key") else dict(s) for s in servers]


async def sync_arr_from_seerr(seerr_url: str, seerr_api_key: str) -> dict:
    """Import Radarr/Sonarr instances from Seerr and merge with existing ones.

    Returns {"radarr_servers": list, "sonarr_servers": list, "message": str}.
    Raises ValueError on bad input, httpx errors on connectivity issues.
    """
    base = (seerr_url or "").rstrip("/")
    key  = seerr_api_key or ""

    # If frontend sent masked value, reuse the stored key — but only toward the
    # registered Seerr URL, otherwise it would be sent to an arbitrary host.
    if key == _MASK or not key:
        stored_url = await get_setting("seerr_url") or ""
        if not base or _normalize_base_url(base) != _normalize_base_url(stored_url):
            raise ValueError("Ressaisissez la clé API Seerr pour cette URL")
        key = await get_setting("seerr_api_key") or ""

    if not base or not key:
        raise ValueError("URL et clé API Seerr requis")

    blocked = await _reject_if_loopback_or_link_local(base)
    if blocked:
        raise ValueError(blocked)

    headers = {"X-Api-Key": key}
    imported_radarr: list[dict] = []
    imported_sonarr: list[dict] = []

    async with httpx.AsyncClient(
        timeout=TIMEOUT_SHORT, follow_redirects=False,
        event_hooks={"request": [_ssrf_guard_hook]},
    ) as c:
        for endpoint in ("/api/v1/settings/radarr", "/api/v1/service/radarr"):
            rr = await _seerr_get(c, f"{base}{endpoint}", headers)
            logger.info("Seerr radarr %s → HTTP %s", endpoint, rr.status_code)
            if rr.status_code == 200:
                data = rr.json()
                if isinstance(data, list) and data:
                    for srv in data:
                        imported_radarr.append({
                            "id":      f"seerr-radarr-{srv.get('id')}",
                            "name":    srv.get("name") or "Radarr",
                            "url":     _build_arr_url(srv),
                            "api_key": srv.get("apiKey") or "",
                            "enabled": True,
                        })
                    break

        for endpoint in ("/api/v1/settings/sonarr", "/api/v1/service/sonarr"):
            rs = await _seerr_get(c, f"{base}{endpoint}", headers)
            logger.info("Seerr sonarr %s → HTTP %s", endpoint, rs.status_code)
            if rs.status_code == 200:
                data = rs.json()
                if isinstance(data, list) and data:
                    for srv in data:
                        imported_sonarr.append({
                            "id":      f"seerr-sonarr-{srv.get('id')}",
                            "name":    srv.get("name") or "Sonarr",
                            "url":     _build_arr_url(srv),
                            "api_key": srv.get("apiKey") or "",
                            "enabled": True,
                        })
                    break

    if not imported_radarr and not imported_sonarr:
        return {"radarr_servers": [], "sonarr_servers": [], "message": "Aucune instance trouvée dans Seerr"}

    def _merge(existing_json: str, imported: list[dict]) -> tuple[list[dict], int]:
        """Return (merged servers, number of newly added servers)."""
        try:
            existing = json.loads(existing_json or "[]") or []
        except Exception:
            existing = []
        existing_urls = {s.get("url", "").rstrip("/") for s in existing}
        new_ones = [s for s in imported if s["url"].rstrip("/") not in existing_urls]
        return new_ones + existing, len(new_ones)

    raw_radarr = await get_setting("radarr_servers") or "[]"
    raw_sonarr  = await get_setting("sonarr_servers") or "[]"
    merged_radarr, added_r = _merge(raw_radarr, imported_radarr)
    merged_sonarr, added_s = _merge(raw_sonarr, imported_sonarr)

    await set_setting("radarr_servers", json.dumps(merged_radarr))
    await set_setting("sonarr_servers", json.dumps(merged_sonarr))

    return {
        "radarr_servers": _mask_servers(merged_radarr),
        "sonarr_servers": _mask_servers(merged_sonarr),
        "message": f"{added_r} Radarr et {added_s} Sonarr ajouté(s)",
    }
