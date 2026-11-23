"""Resolve a queue row to the real media-server item Id.

Consolidated queue rows (``sonarr-series:N`` / ``sonarr-season:N``) carry no
per-item Emby/Jellyfin Id; the matching Series/Season library item is found by
its on-disk path (same logic as the deletion pipeline).
"""
import logging
import os
from typing import Optional

from .arr_clients import sonarr_get_series_by_id_any
from .emby_client import find_item_by_path

logger = logging.getLogger(__name__)

_CONSOLIDATED_PREFIXES = ("sonarr-series:", "sonarr-season:")


async def resolve_server_item_id(row: dict, server_id: str) -> Optional[str]:
    """Return the real server Id for ``row``, or None if it cannot be resolved."""
    emby_id = row.get("emby_id")
    if not emby_id:
        return None
    if not str(emby_id).startswith(_CONSOLIDATED_PREFIXES):
        return emby_id

    series_id = row.get("sonarr_series_id")
    if not series_id:
        return None
    series = await sonarr_get_series_by_id_any(
        int(series_id), arr_server_url=row.get("arr_server_url")
    )
    series_path = (series or {}).get("path") or ""
    if not series_path:
        return None

    if row.get("season_number") is not None:
        season_path = os.path.dirname(row.get("file_path") or "")
        if not season_path:
            return None
        target = await find_item_by_path(season_path, include_types="Season", server_id=server_id)
    else:
        target = await find_item_by_path(series_path, include_types="Series", server_id=server_id)

    if not target:
        return None
    return str(target["Id"])
