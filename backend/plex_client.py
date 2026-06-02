# backend/plex_client.py
"""Plex local REST API client (port 32400, X-Plex-Token auth, JSON responses)."""
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from .db.utils import TIMEOUT_MEDIUM, TIMEOUT_SHORT

logger = logging.getLogger(__name__)

_PLEX_HEADERS = {
    "Accept": "application/json",
    "X-Plex-Client-Identifier": "hygie-v3",
    "X-Plex-Product": "Hygie",
    "X-Plex-Version": "3.0",
}


def _ts_to_iso(ts: Optional[int]) -> Optional[str]:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _extract_tmdb_id(guids: list) -> str:
    for g in guids or []:
        gid = g.get("id", "")
        if gid.startswith("tmdb://"):
            return gid[7:]
    return ""


class PlexClient:
    """Async HTTP client for a single Plex Media Server."""

    def __init__(self, url: str, token: str) -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._headers = {**_PLEX_HEADERS, "X-Plex-Token": token}

    async def _get(self, path: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as client:
            resp = await client.get(
                f"{self._url}{path}",
                headers=self._headers,
                params=params or {},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_libraries(self) -> list[dict]:
        data = await self._get("/library/sections")
        dirs = data.get("MediaContainer", {}).get("Directory") or []
        if isinstance(dirs, dict):
            dirs = [dirs]
        return [{"id": d["key"], "title": d["title"], "type": d["type"]} for d in dirs]

    def _normalize_item(self, m: dict) -> dict:
        guids = m.get("Guid") or []
        if isinstance(guids, dict):
            guids = [guids]

        thumb = m.get("thumb", "")
        poster_url = f"{self._url}{thumb}?X-Plex-Token={self._token}" if thumb else ""

        media_type = m.get("type", "movie")
        if media_type == "show":
            media_type = "series"

        return {
            "plex_id":           m.get("ratingKey", ""),
            "title":             m.get("title", ""),
            "media_type":        media_type,
            "view_count":        int(m.get("viewCount") or 0),
            "last_viewed_at":    _ts_to_iso(m.get("lastViewedAt")),
            "added_at":          _ts_to_iso(m.get("addedAt")),
            "duration_ms":       int(m.get("duration") or 0),
            "rating":            float(m.get("rating") or 0),
            "poster_url":        poster_url,
            "tmdb_id":           _extract_tmdb_id(guids),
            "grandparent_title": m.get("grandparentTitle", ""),
            "season_number":     m.get("parentIndex"),
            "raw":               m,
        }

    async def scan_library(self, section_id: str) -> list[dict]:
        data = await self._get(f"/library/sections/{section_id}/all")
        metadata = data.get("MediaContainer", {}).get("Metadata") or []
        if isinstance(metadata, dict):
            metadata = [metadata]
        return [self._normalize_item(m) for m in metadata]

    async def get_item_metadata(self, rating_key: str) -> Optional[dict]:
        try:
            data = await self._get(f"/library/metadata/{rating_key}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        metadata = data.get("MediaContainer", {}).get("Metadata") or []
        if not metadata:
            return None
        if isinstance(metadata, dict):
            metadata = [metadata]
        return self._normalize_item(metadata[0])

    async def delete_item(self, rating_key: str) -> bool:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as client:
            resp = await client.delete(
                f"{self._url}/library/metadata/{rating_key}",
                headers=self._headers,
            )
        if resp.status_code == 404:
            logger.warning("Plex delete: item %s not found", rating_key)
            return False
        resp.raise_for_status()
        logger.info("Plex: deleted item ratingKey=%s", rating_key)
        return True

    async def upload_poster(self, rating_key: str, image_bytes: bytes) -> bool:
        """Upload image_bytes as the custom poster for this item (sets as selected)."""
        async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as client:
            resp = await client.post(
                f"{self._url}/library/metadata/{rating_key}/posters",
                headers={**self._headers, "Content-Type": "image/jpeg"},
                content=image_bytes,
            )
        if resp.status_code not in (200, 201, 204):
            logger.warning("Plex upload_poster HTTP %s for ratingKey=%s", resp.status_code, rating_key)
            return False
        return True

    async def restore_poster(self, rating_key: str) -> bool:
        """Restore original poster by triggering a Plex metadata refresh from agents."""
        async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as client:
            resp = await client.put(
                f"{self._url}/library/metadata/{rating_key}/refresh",
                headers=self._headers,
            )
        return resp.status_code in (200, 204)

    async def get_active_sessions(self) -> list[dict]:
        data = await self._get("/status/sessions")
        metadata = data.get("MediaContainer", {}).get("Metadata") or []
        if isinstance(metadata, dict):
            metadata = [metadata]
        sessions = []
        for m in metadata:
            user = m.get("User") or {}
            sessions.append({
                "plex_id":       m.get("ratingKey", ""),
                "title":         m.get("title", ""),
                "media_type":    m.get("type", ""),
                "username":      user.get("title", ""),
                "user_id":       user.get("id", ""),
                "view_offset_ms": int(m.get("viewOffset") or 0),
            })
        return sessions

    async def get_recently_added(self, section_id: str, limit: int = 50) -> list[dict]:
        data = await self._get(
            f"/library/sections/{section_id}/recentlyAdded",
            params={"X-Plex-Container-Size": str(limit)},
        )
        metadata = data.get("MediaContainer", {}).get("Metadata") or []
        if isinstance(metadata, dict):
            metadata = [metadata]
        return [self._normalize_item(m) for m in metadata]

    async def search(self, query: str) -> list[dict]:
        data = await self._get("/hubs/search", params={"query": query, "limit": "20"})
        hubs = data.get("MediaContainer", {}).get("Hub") or []
        if isinstance(hubs, dict):
            hubs = [hubs]
        results = []
        for hub in hubs:
            metadata = hub.get("Metadata") or []
            if isinstance(metadata, dict):
                metadata = [metadata]
            results.extend(self._normalize_item(m) for m in metadata)
        return results


async def test_plex_server(server: dict) -> tuple[bool, str, str]:
    """Test a Plex server connection. Returns (ok, message, server_type)."""
    url = (server.get("url") or "").rstrip("/")
    token = server.get("api_key") or ""
    if not url or not token:
        return False, "URL ou token manquant", "plex"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as client:
            resp = await client.get(
                f"{url}/identity",
                headers={**_PLEX_HEADERS, "X-Plex-Token": token},
            )
            if resp.status_code == 200:
                data = resp.json()
                mc = data.get("MediaContainer", {})
                version = mc.get("version", "?")
                return True, f"Plex Media Server v{version}", "plex", ""
            codes = {401: "http_401", 403: "http_403", 404: "http_404", 502: "http_502", 503: "http_503"}
            return False, f"HTTP {resp.status_code}", "plex", codes.get(resp.status_code, f"http_{resp.status_code}")
    except Exception as e:
        s = str(e).lower()
        if "name or service not known" in s or "errno -2" in s:
            code = "dns_failure"
        elif "connection refused" in s or "errno 111" in s:
            code = "connection_refused"
        elif "timed out" in s or "timeout" in s:
            code = "timeout"
        else:
            code = "network_error"
        return False, str(e), "plex", code


def build_plex_client(server: dict) -> Optional["PlexClient"]:
    if server.get("type") != "plex":
        return None
    url = server.get("url", "").rstrip("/")
    token = server.get("api_key", "")
    if not url or not token:
        logger.warning("Plex server %s has no URL or token", server.get("id"))
        return None
    return PlexClient(url=url, token=token)
