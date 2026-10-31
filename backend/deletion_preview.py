"""Read-only preview of what deleting a queue item will touch.

Whole-series rows remove a series (files, torrents, cross-seed copies) in one
click; this shows the scope first. It reuses the removal planner of the real
pipeline, so the preview cannot promise something the deletion won't do. A
shared season pack can still be kept at deletion time (that check needs the
post-Sonarr state), which the preview states instead of guessing.
"""
import logging
from typing import Optional

from .arr_clients import sonarr_get_files_size, sonarr_series_file_count
from .db.settings_store import get_bool_setting, get_setting
from .deletion_helpers import _find_torrent_hashes_consolidated, _is_consolidated_row
from .qbit_client import qbit_list_torrents
from .qbit_siblings import build_plan

logger = logging.getLogger(__name__)


def _scope(row: dict) -> str:
    if row.get("media_type") == "Movie":
        return "movie"
    if _is_consolidated_row(row):
        return "season" if row.get("season_number") is not None else "series"
    return "episode"


async def _files(row: dict, scope: str) -> Optional[dict]:
    if scope not in ("series", "season"):
        return None
    srv = {"arr_server_url": row["arr_server_url"]} if row.get("arr_server_url") else {}
    series_id = int(row["sonarr_series_id"])
    size = await sonarr_get_files_size(
        series_id, season_number=row.get("season_number") if scope == "season" else None, **srv)
    count = await sonarr_series_file_count(series_id, **srv) if scope == "series" else None
    if size is None and count is None:
        return None          # Sonarr silent: say "unknown", never "0 B"
    return {"count": count, "bytes": size}


async def _torrents(row: dict) -> tuple[Optional[list], int, Optional[str]]:
    """(entries, total, warning). entries is None when no plan could be made."""
    hashes = await _find_torrent_hashes_consolidated(row)
    if not hashes:
        # The lookup swallows Sonarr errors and answers "no hash": it cannot tell
        # "nothing to remove" from "could not look", so never present it as safe.
        return None, 0, "Aucun torrent associé trouvé (Sonarr n'a peut-être pas répondu) : l'effet sur qBittorrent est inconnu"
    twins = await get_bool_setting("qbit_delete_linked_crossseeds")
    try:
        plan = await build_plan(set(hashes), include_twins=twins)
    except Exception as e:
        logger.warning("deletion preview: plan failed for '%s': %s", row.get("title", "?"), e)
        return None, 0, f"Plan qBittorrent impossible : {e}"
    if plan is None:
        return None, 0, "qBittorrent injoignable : liste des torrents indisponible"
    names = {t["hash"].lower(): str(t.get("name") or "") for t in (await qbit_list_torrents() or [])}
    entries = [
        {"hash": r.hash, "name": names.get(r.hash.lower(), ""), "twin": r.twin,
         "erase_files": r.delete_files, "reason": r.reason}
        for r in plan
    ]
    return entries, len(entries), None


async def build_preview(row: dict) -> dict:
    scope = _scope(row)
    action = (await get_setting("qbit_action") or "").strip()
    out = {"scope": scope, "title": row.get("title", ""), "files": None, "qbit_action": action,
           "torrents": None, "torrents_total": 0, "warning": None}
    if scope in ("series", "season"):
        out["files"] = await _files(row, scope)
        if out["files"] is None:
            out["warning"] = "Sonarr n'a pas répondu : étendue de la suppression inconnue"
        if action == "delete_files":
            torrents, out["torrents_total"], torrent_warning = await _torrents(row)
            out["torrents"] = torrents
            out["warning"] = out["warning"] or torrent_warning
    return out
