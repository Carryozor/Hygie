"""Arr service — business logic for Radarr/Sonarr/Seerr operations.

Extracted from routers/settings.py to separate HTTP concerns from domain logic.
"""
import json
import logging

import httpx

from ..db.settings_store import get_setting, set_setting
from ..db.utils import TIMEOUT_SHORT

logger = logging.getLogger(__name__)

_MASK = "***"


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

    if not url or not key:
        return {"ok": False, "message": "URL et clé API requis"}

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


async def sync_arr_from_seerr(seerr_url: str, seerr_api_key: str) -> dict:
    """Import Radarr/Sonarr instances from Seerr and merge with existing ones.

    Returns {"radarr_servers": list, "sonarr_servers": list, "message": str}.
    Raises ValueError on bad input, httpx errors on connectivity issues.
    """
    base = (seerr_url or "").rstrip("/")
    key  = seerr_api_key or ""

    # If frontend sent masked value, read real key from DB
    if key == _MASK or not key:
        key = await get_setting("seerr_api_key") or ""

    if not base or not key:
        raise ValueError("URL et clé API Seerr requis")

    headers = {"X-Api-Key": key}
    imported_radarr: list[dict] = []
    imported_sonarr: list[dict] = []

    async with httpx.AsyncClient(timeout=TIMEOUT_SHORT, follow_redirects=True) as c:
        for endpoint in ("/api/v1/settings/radarr", "/api/v1/service/radarr"):
            rr = await c.get(f"{base}{endpoint}", headers=headers)
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
            rs = await c.get(f"{base}{endpoint}", headers=headers)
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

    def _merge(existing_json: str, imported: list[dict]) -> list[dict]:
        try:
            existing = json.loads(existing_json or "[]") or []
        except Exception:
            existing = []
        existing_urls = {s.get("url", "").rstrip("/") for s in existing}
        new_ones = [s for s in imported if s["url"].rstrip("/") not in existing_urls]
        return new_ones + existing

    raw_radarr = await get_setting("radarr_servers") or "[]"
    raw_sonarr  = await get_setting("sonarr_servers") or "[]"
    merged_radarr = _merge(raw_radarr, imported_radarr)
    merged_sonarr  = _merge(raw_sonarr, imported_sonarr)

    await set_setting("radarr_servers", json.dumps(merged_radarr))
    await set_setting("sonarr_servers", json.dumps(merged_sonarr))

    prev_r = len(json.loads(raw_radarr or "[]") or [])
    prev_s = len(json.loads(raw_sonarr or "[]") or [])
    added_r = max(0, len(merged_radarr) - prev_r)
    added_s = max(0, len(merged_sonarr) - prev_s)

    return {
        "radarr_servers": merged_radarr,
        "sonarr_servers": merged_sonarr,
        "message": f"{added_r} Radarr et {added_s} Sonarr ajouté(s)",
    }
