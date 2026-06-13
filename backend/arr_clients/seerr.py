"""Seerr / Overseerr / Jellyseerr API client."""
import asyncio
import logging
from typing import Optional, List

import httpx

from ..db.settings_store import get_setting
from ..db.utils import DB_PATH, TIMEOUT_SHORT, TIMEOUT_MEDIUM, TIMEOUT_LONG
from ..db.engine import get_db
from ..exceptions import ArrClientError

logger = logging.getLogger(__name__)


async def _seerr_config():
    url = (await get_setting("seerr_url") or "").rstrip("/")
    key = await get_setting("seerr_api_key") or ""
    return url, key


async def _seerr_pages(
    client: "httpx.AsyncClient",
    url: str,
    headers: dict,
    params: dict | None = None,
):
    """Async generator — yields each page's results list from a take/skip endpoint."""
    skip = 0
    while True:
        r = await client.get(
            url, headers=headers,
            params={**(params or {}), "take": 100, "skip": skip},
        )
        if r.status_code != 200:
            logger.warning("_seerr_pages: HTTP %s from %s (skip=%s)", r.status_code, url, skip)
            break
        data = r.json()
        page = data.get("results", []) if isinstance(data, dict) else data
        total = data.get("pageInfo", {}).get("results", len(page))
        if not page:
            break
        yield page
        if skip + 100 >= total:
            break
        skip += 100


async def test_seerr() -> tuple[bool, str]:
    url, key = await _seerr_config()
    if not url or not key:
        return False, "Non configuré"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as c:
            r = await c.get(f"{url}/api/v1/status", headers={"X-Api-Key": key})
            if r.status_code == 200:
                return True, f"Seerr {r.json().get('version', '?')}"
            return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


def _extract_discord_id(notif: dict) -> str:
    """Extract the Discord ID from a Seerr notification-settings payload.

    Recent Seerr/Jellyseerr versions return `discordIds` (list of strings);
    older versions returned a single `discordId` string. Support both.
    When several IDs are linked, the first entry wins (Hygie mentions one
    Discord account per Seerr user).
    """
    ids = notif.get("discordIds")
    if isinstance(ids, (list, tuple)) and ids:
        return str(ids[0] or "").strip()
    return str(notif.get("discordId") or "").strip()


async def seerr_get_users() -> List[dict]:
    """
    List all Seerr users with their Discord IDs.
    discord_id comes from two sources (merged):
      - Seerr user notification settings (discordIds field, legacy discordId)
      - Hygie seerr_user_rules table (manually configured)
    The Hygie manual mapping takes priority if both are set.
    """
    url, key = await _seerr_config()
    if not url or not key:
        return []
    out = []
    try:
        hygie_mappings: dict = {}
        try:
            async with get_db() as db:
                rows = await db.fetch_all(
                    "SELECT CAST(seerr_user_id AS TEXT) AS uid, discord_id FROM seerr_user_rules "
                    "WHERE discord_id IS NOT NULL AND TRIM(discord_id) != ''"
                )
                for row in rows:
                    hygie_mappings[str(row["uid"])] = row["discord_id"].strip()
        except Exception:
            pass

        async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as c:
            all_users = []
            async for users_page in _seerr_pages(c, f"{url}/api/v1/user", {"X-Api-Key": key}):
                all_users.extend(users_page)

            sem = asyncio.Semaphore(10)

            async def _fetch_discord(u: dict) -> dict:
                uid = u.get("id")
                name = (
                    u.get("displayName")
                    or u.get("username")
                    or u.get("email")
                    or f"User #{uid}"
                )
                seerr_discord = ""
                async with sem:
                    try:
                        rn = await c.get(
                            f"{url}/api/v1/user/{uid}/settings/notifications",
                            headers={"X-Api-Key": key},
                        )
                        if rn.status_code == 200:
                            seerr_discord = _extract_discord_id(rn.json())
                    except Exception:
                        pass
                hygie_discord = hygie_mappings.get(str(uid), "")
                return {
                    "id": uid,
                    "username": name,
                    "discord_id": hygie_discord or seerr_discord,
                    "discord_id_seerr": seerr_discord,
                    "discord_id_hygie": hygie_discord,
                }

            results = await asyncio.gather(*[_fetch_discord(u) for u in all_users])
            out.extend(results)
    except Exception as e:
        logger.warning(f"seerr_get_users: {e}")
    return out


async def build_seerr_request_cache() -> dict:
    """Build {tmdb_id: {seerr_id, user_id, username}} for all Seerr requests.

    One paginated scan instead of one per media item during scan.
    Falls back to empty dict if Seerr is unreachable.
    """
    url, key = await _seerr_config()
    if not url or not key:
        return {}

    async def _fetch() -> dict:
        cache: dict = {}
        async with httpx.AsyncClient(timeout=TIMEOUT_LONG) as c:
            async for items in _seerr_pages(
                c, f"{url}/api/v1/request", {"X-Api-Key": key},
                {"sort": "added", "filter": "all"},
            ):
                for req in items:
                    media = req.get("media") or {}
                    tmdb_id = str(media.get("tmdbId") or "")
                    if not tmdb_id:
                        continue
                    user = req.get("requestedBy") or {}
                    # setdefault: first request wins (oldest, most likely the primary requester)
                    cache.setdefault(tmdb_id, {
                        "seerr_id": media.get("id"),
                        "user_id": user.get("id"),
                        "username": (
                            user.get("displayName")
                            or user.get("username")
                            or user.get("email")
                            or ""
                        ),
                    })
        return cache

    from .circuit_breaker import get_breaker, CircuitOpenError
    try:
        # Callers handle ArrClientError gracefully (WARN + optional Discord
        # alert) — an OPEN breaker must surface the same way, just faster.
        return await get_breaker("seerr").call(_fetch)
    except ArrClientError:
        raise
    except CircuitOpenError as e:
        raise ArrClientError(str(e)) from e
    except Exception as e:
        raise ArrClientError(f"Seerr inaccessible: {e}") from e


async def seerr_find_request_by_tmdb(tmdb_id: str) -> Optional[dict]:
    """Find a Seerr request by tmdbId. Returns dict with id, user_id, username."""
    url, key = await _seerr_config()
    if not url or not key or not tmdb_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as c:
            async for items in _seerr_pages(
                c, f"{url}/api/v1/request", {"X-Api-Key": key},
                {"sort": "added", "filter": "all"},
            ):
                for req in items:
                    media = req.get("media") or {}
                    if str(media.get("tmdbId") or "") == str(tmdb_id):
                        user = req.get("requestedBy") or {}
                        return {
                            "seerr_id": media.get("id"),
                            "user_id": user.get("id"),
                            "username": (
                                user.get("displayName")
                                or user.get("username")
                                or user.get("email")
                                or ""
                            ),
                        }
    except Exception as e:
        logger.warning(f"seerr_find_request_by_tmdb: {e}")
    return None


async def seerr_delete_request(media_id: int) -> bool:
    """Delete a Seerr media (by media.id, not request.id)."""
    url, key = await _seerr_config()
    if not url or not key or not media_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as c:
            r = await c.delete(
                f"{url}/api/v1/media/{media_id}", headers={"X-Api-Key": key}
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"seerr_delete_request: {e}")
        return False
