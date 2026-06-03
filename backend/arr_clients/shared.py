"""Shared helpers used by Radarr, Sonarr, and Seerr clients."""
import logging

from ..db.settings_store import get_setting

logger = logging.getLogger(__name__)


def _arr_auth(key: str) -> dict:
    """Return X-Api-Key header for Radarr/Sonarr."""
    return {"X-Api-Key": key}


def _path_matches(file_path: str, item_path: str, folder: str) -> bool:
    """Return True if file_path matches an arr item (exact path or inside folder)."""
    return bool(item_path and item_path == file_path) or bool(
        folder and file_path.startswith(folder.rstrip("/") + "/")
    )
