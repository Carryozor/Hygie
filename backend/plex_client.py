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


def build_plex_client(server: dict) -> Optional["PlexClient"]:
    if server.get("type") != "plex":
        return None
    url = server.get("url", "").rstrip("/")
    token = server.get("api_key", "")
    if not url or not token:
        logger.warning("Plex server %s has no URL or token", server.get("id"))
        return None
    return PlexClient(url=url, token=token)
