# backend/plex_tv_client.py
"""Plex.tv cloud API client — shared users, server discovery, account info."""
import logging

import httpx

from .db.utils import TIMEOUT_MEDIUM

logger = logging.getLogger(__name__)

_PLEX_TV_BASE = "https://plex.tv/api/v2"
_PLEX_TV_HEADERS = {
    "Accept": "application/json",
    "X-Plex-Client-Identifier": "hygie-v3",
    "X-Plex-Product": "Hygie",
}


class PlexTVClient:
    """Async client for Plex.tv cloud API."""

    def __init__(self, token: str) -> None:
        self._token = token
        self._headers = {**_PLEX_TV_HEADERS, "X-Plex-Token": token}

    async def _get(self, path: str, params: dict | None = None) -> dict | list:
        async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as client:
            resp = await client.get(
                f"{_PLEX_TV_BASE}{path}",
                headers=self._headers,
                params=params or {},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_user(self) -> dict:
        data = await self._get("/user")
        return {
            "id":       data.get("id"),
            "username": data.get("username", ""),
            "email":    data.get("email", ""),
            "title":    data.get("title", ""),
        }

    async def validate_token(self) -> bool:
        try:
            await self._get("/user")
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                return False
            raise

    async def get_friends(self) -> list[dict]:
        data = await self._get("/friends")
        friends = data if isinstance(data, list) else []
        return [
            {
                "plex_user_id": f.get("id"),
                "username":     f.get("username", ""),
                "email":        f.get("email", ""),
                "title":        f.get("title", ""),
                "thumb":        f.get("thumb", ""),
            }
            for f in friends
        ]

    async def get_servers(self) -> list[dict]:
        data = await self._get("/resources", params={"includeHttps": "1", "includeRelay": "1"})
        resources = data if isinstance(data, list) else []
        servers = []
        for r in resources:
            if r.get("product") != "Plex Media Server":
                continue
            if "server" not in (r.get("provides") or ""):
                continue
            connections = r.get("connections") or []
            local_url = next(
                (c["uri"] for c in connections if c.get("local") and not c.get("relay")), ""
            )
            remote_url = next(
                (c["uri"] for c in connections if not c.get("local") and not c.get("relay")), ""
            )
            if not local_url and not remote_url:
                continue
            servers.append({
                "name":          r.get("name", ""),
                "identifier":    r.get("clientIdentifier", ""),
                "local_url":     local_url,
                "remote_url":    remote_url,
                "preferred_url": local_url or remote_url,
            })
        return servers
