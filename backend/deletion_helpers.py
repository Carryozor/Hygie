# backend/deletion_helpers.py
"""Leaf helpers shared by deletion.py and deletion_pipeline.py.

Kept import-free of both modules so they can depend on it without a cycle.
"""
import logging
from typing import Optional

from .db.logs import add_log
from .arr_clients import (
    radarr_delete, radarr_find_by_path, radarr_get_torrent_hash,
    radarr_delete_by_id, radarr_get_torrent_hash_any,
    seerr_delete_request, sonarr_delete_episode_file, sonarr_delete_season,
    sonarr_delete_series, sonarr_find_by_path, sonarr_find_by_path_full,
    sonarr_get_torrent_hash,
)
from .qbit_client import qbit_add_tag, qbit_delete_torrent, qbit_find_by_path
from .logmsg import lm

logger = logging.getLogger(__name__)


async def _find_torrent_hash(row: dict) -> Optional[str]:
    """Find torrent hash via Radarr/Sonarr history, with qBit path fallback.

    Must be called BEFORE removing from arr — history disappears after deletion.
    """
    file_path = row.get("file_path", "")
    media_type = row.get("media_type", "")
    if media_type == "Movie":
        rid_stored = row.get("radarr_id")
        if rid_stored:
            return await radarr_get_torrent_hash_any(int(rid_stored))
        found = await radarr_find_by_path(file_path)
        if found:
            rid, r_url, r_key = found
            return await radarr_get_torrent_hash(int(rid), url=r_url, key=r_key)
    else:
        sid = row.get("sonarr_id") or await sonarr_find_by_path(file_path)
        if sid:
            return await sonarr_get_torrent_hash(int(sid))
    if file_path:
        return await qbit_find_by_path(file_path)
    return None


async def _find_torrent_hashes_consolidated(row: dict) -> set:
    """Resolve every distinct torrent hash backing a consolidated season/series entry.

    Must run BEFORE the bulk Sonarr file delete — the lookup matches history
    records to the (still-existing) episode file records.
    """
    if not _is_consolidated_row(row):
        return set()
    from .arr_clients import sonarr_get_torrent_hashes_for_group
    return await sonarr_get_torrent_hashes_for_group(
        int(row["sonarr_series_id"]), season_number=row.get("season_number")
    )


def _is_consolidated_row(row: dict) -> bool:
    """True for the consolidated season/series queue rows produced by
    deletion_unit=season|series — one row stands in for a whole group of
    episodes and carries sonarr_series_id but no per-item sonarr_id.

    A normal per-episode row (deletion_unit=episode) carries sonarr_id (its
    own episodeFile id) *and* sonarr_series_id/season_number — the scanner
    sets all three together for every regular episode — so sonarr_series_id
    alone is not a safe discriminator; without the sonarr_id check, a single
    episode's delete was previously misrouted into sonarr_delete_season(),
    wiping every file in that whole season instead of just the one due.
    """
    return bool(row.get("sonarr_series_id")) and not row.get("sonarr_id")


async def _delete_from_arr(row: dict) -> bool:
    """Remove media from Radarr or Sonarr. Returns False only on a genuine
    removal failure — an item with no arr link to begin with (never matched,
    or already removed) is not a failure."""
    file_path = row.get("file_path", "")
    media_type = row.get("media_type", "")
    title = row.get("title", "?")
    sonarr_series_id = row.get("sonarr_series_id")
    season_number = row.get("season_number")
    arr_server_url = row.get("arr_server_url")
    consolidated = _is_consolidated_row(row)

    if media_type == "Movie":
        rid_stored = row.get("radarr_id")
        if rid_stored:
            ok = await radarr_delete_by_id(
                int(rid_stored), delete_files=False,
                arr_server_url=arr_server_url, file_path=file_path,
            )
            await add_log("DEBUG" if ok else "WARN", lm("radarr.removed" if ok else "radarr.remove_err", title=title), "deletion")
            return ok
        else:
            found = await radarr_find_by_path(file_path)
            if found:
                rid, r_url, r_key = found
                ok = await radarr_delete(int(rid), delete_files=False, url=r_url, key=r_key)
                await add_log("DEBUG" if ok else "WARN", lm("radarr.removed" if ok else "radarr.remove_err", title=title), "deletion")
                return ok
            return True
    elif consolidated and season_number is not None:
        # Season-level consolidated entry
        ok = await sonarr_delete_season(
            int(sonarr_series_id), int(season_number),
            arr_server_url=arr_server_url, file_path=file_path,
        )
        await add_log("DEBUG" if ok else "WARN", lm("sonarr.season_ok" if ok else "sonarr.season_err", title=title, n=season_number), "deletion")
        return ok
    elif consolidated:
        # Series-level consolidated entry
        ok = await sonarr_delete_series(
            int(sonarr_series_id),
            arr_server_url=arr_server_url, file_path=file_path,
        )
        await add_log("DEBUG" if ok else "WARN", lm("sonarr.series_ok" if ok else "sonarr.series_err", title=title), "deletion")
        return ok
    else:
        sid = row.get("sonarr_id")
        if sid:
            ok = await sonarr_delete_episode_file(
                int(sid), arr_server_url=arr_server_url, file_path=file_path,
            )
            await add_log("DEBUG" if ok else "WARN", lm("sonarr.removed" if ok else "sonarr.remove_err", title=title), "deletion")
            return ok
        found = await sonarr_find_by_path_full(file_path)
        if found:
            ef_id, s_url, s_key = found
            ok = await sonarr_delete_episode_file(int(ef_id), url=s_url, key=s_key)
            await add_log("DEBUG" if ok else "WARN", lm("sonarr.removed" if ok else "sonarr.remove_err", title=title), "deletion")
            return ok
        return True


async def _delete_from_seerr(row: dict) -> None:
    """Delete the Seerr request linked to this media, if any."""
    if row.get("seerr_id"):
        await seerr_delete_request(row["seerr_id"])
        await add_log("DEBUG", lm("seerr.deleted", title=row.get('title','?')), "deletion")


async def _handle_qbit(torrent_hash: str, title: str, qbit_action: str, qbit_tag: str) -> None:
    """Tag or delete the torrent in qBittorrent based on configured action."""
    try:
        if qbit_action in ("delete_torrent", "delete_files"):
            ok = await qbit_delete_torrent(torrent_hash, delete_files=True)
            msg = (lm("qbit.torrent_deleted", title=title)
                   if ok else lm("qbit.torrent_fail", title=title))
            await add_log("INFO" if ok else "WARN", msg, "deletion")
        else:
            ok = await qbit_add_tag(torrent_hash, qbit_tag)
            msg = (lm("qbit.tag_added", tag=qbit_tag, title=title)
                   if ok else lm("qbit.tag_fail", title=title))
            await add_log("INFO" if ok else "WARN", msg, "deletion")
    except Exception as e:
        await add_log("WARN", lm("qbit.error", title=title, detail=e), "deletion")
