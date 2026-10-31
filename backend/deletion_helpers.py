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
from .qbit_client import (
    qbit_add_tag, qbit_delete_torrent, qbit_find_by_path, qbit_pause_torrent,
)
from .logmsg import lm

logger = logging.getLogger(__name__)


async def _find_torrent_hash(row: dict) -> Optional[str]:
    """Find torrent hash via Radarr/Sonarr history, with qBit path fallback.

    Must be called BEFORE removing from arr — history disappears after deletion.
    """
    file_path = row.get("file_path", "")
    media_type = row.get("media_type", "")
    srv = {"arr_server_url": row["arr_server_url"]} if row.get("arr_server_url") else {}
    if media_type == "Movie":
        history_hash = None
        rid_stored = row.get("radarr_id")
        if rid_stored:
            history_hash = await radarr_get_torrent_hash_any(int(rid_stored), **srv)
        else:
            found = await radarr_find_by_path(file_path)
            if found:
                rid, r_url, r_key = found
                history_hash = await radarr_get_torrent_hash(int(rid), url=r_url, key=r_key)
        if history_hash:
            return history_hash
    else:
        sid = row.get("sonarr_id") or await sonarr_find_by_path(file_path)
        if sid:
            return await sonarr_get_torrent_hash(int(sid), **srv)
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
    srv = {"arr_server_url": row["arr_server_url"]} if row.get("arr_server_url") else {}
    return await sonarr_get_torrent_hashes_for_group(
        int(row["sonarr_series_id"]), season_number=row.get("season_number"), **srv
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


async def _delete_from_arr(row: dict, *, missing_ok: bool = False) -> bool:
    """Remove media from Radarr or Sonarr. Returns False only on a genuine
    removal failure — an item with no arr link to begin with (never matched,
    or already removed) is not a failure.

    ``missing_ok`` (retry of an interrupted attempt only): a 404 from the arr
    means "already removed" and counts as success."""
    file_path = row.get("file_path", "")
    media_type = row.get("media_type", "")
    title = row.get("title", "?")
    sonarr_series_id = row.get("sonarr_series_id")
    season_number = row.get("season_number")
    arr_server_url = row.get("arr_server_url")
    consolidated = _is_consolidated_row(row)
    extra = {"missing_ok": True} if missing_ok else {}

    if media_type == "Movie":
        rid_stored = row.get("radarr_id")
        if rid_stored:
            ok = await radarr_delete_by_id(
                int(rid_stored), delete_files=False,
                arr_server_url=arr_server_url, file_path=file_path, **extra,
            )
            await add_log("DEBUG" if ok else "WARN", lm("radarr.removed" if ok else "radarr.remove_err", title=title), "deletion")
            return ok
        else:
            found = await radarr_find_by_path(file_path)
            if found:
                rid, r_url, r_key = found
                ok = await radarr_delete(int(rid), delete_files=False, url=r_url, key=r_key, **extra)
                await add_log("DEBUG" if ok else "WARN", lm("radarr.removed" if ok else "radarr.remove_err", title=title), "deletion")
                return ok
            return True
    elif consolidated and season_number is not None:
        # Season-level consolidated entry
        ok = await sonarr_delete_season(
            int(sonarr_series_id), int(season_number),
            arr_server_url=arr_server_url, file_path=file_path, **extra,
        )
        await add_log("DEBUG" if ok else "WARN", lm("sonarr.season_ok" if ok else "sonarr.season_err", title=title, n=season_number), "deletion")
        return ok
    elif consolidated:
        # Series-level consolidated entry
        ok = await sonarr_delete_series(
            int(sonarr_series_id),
            arr_server_url=arr_server_url, file_path=file_path, **extra,
        )
        await add_log("DEBUG" if ok else "WARN", lm("sonarr.series_ok" if ok else "sonarr.series_err", title=title), "deletion")
        return ok
    else:
        sid = row.get("sonarr_id")
        if sid:
            ok = await sonarr_delete_episode_file(
                int(sid), arr_server_url=arr_server_url, file_path=file_path, **extra,
            )
            await add_log("DEBUG" if ok else "WARN", lm("sonarr.removed" if ok else "sonarr.remove_err", title=title), "deletion")
            return ok
        found = await sonarr_find_by_path_full(file_path)
        if found:
            ef_id, s_url, s_key = found
            ok = await sonarr_delete_episode_file(int(ef_id), url=s_url, key=s_key, **extra)
            await add_log("DEBUG" if ok else "WARN", lm("sonarr.removed" if ok else "sonarr.remove_err", title=title), "deletion")
            return ok
        return True


def _is_whole_series_row(row: dict) -> bool:
    """True for the consolidated whole-series queue row (``sonarr-series:N``)."""
    return (str(row.get("emby_id") or "").startswith("sonarr-series:")
            and row.get("season_number") is None)


async def _series_has_no_files(row: dict) -> bool:
    """True only when Sonarr positively reports zero episode files left for
    the row's series. Any doubt (error, unreachable) returns False."""
    try:
        from . import arr_clients
        srv = {"arr_server_url": row["arr_server_url"]} if row.get("arr_server_url") else {}
        count = await arr_clients.sonarr_series_file_count(int(row["sonarr_series_id"]), **srv)
    except Exception as e:
        logger.warning("series file count failed for '%s': %s", row.get("title", "?"), e)
        return False
    return count == 0


async def _shared_pack_blocker(row: dict, torrent_hash: str) -> Optional[str]:
    """Reason why the torrent's FILES must not be deleted, or None if safe.

    A season pack backs several episodes; deleting it for one episode would
    wipe the others still in the library. Checked after the Sonarr removal, so
    any file still linked to the hash belongs to another episode. If Sonarr
    cannot answer, the torrent is kept (data loss outweighs a leftover).
    A whole-series row with no file left in the series is always safe.
    """
    series_id = row.get("sonarr_series_id")
    if not series_id:
        return "série Sonarr inconnue, impossible de vérifier le partage du torrent — conservé"
    # Whole-series row: nothing left in the series => no episode can share the
    # torrent, whatever the (possibly vanished) history says.
    if _is_whole_series_row(row) and await _series_has_no_files(row):
        return None
    try:
        from . import arr_clients
        srv = {"arr_server_url": row["arr_server_url"]} if row.get("arr_server_url") else {}
        remaining = await arr_clients.sonarr_remaining_files_for_hash(
            int(series_id), torrent_hash, **srv)
    except Exception as e:
        logger.warning("shared-pack check failed for '%s': %s", row.get("title", "?"), e)
        remaining = None
    if remaining is None:
        return "vérification du partage du torrent impossible (Sonarr) — conservé"
    if remaining > 0:
        return f"torrent partagé avec {remaining} autre(s) épisode(s) encore présent(s) — conservé"
    return None


async def _delete_from_seerr(row: dict) -> None:
    """Delete the Seerr request linked to this media, if any."""
    if row.get("seerr_id"):
        await seerr_delete_request(row["seerr_id"])
        await add_log("DEBUG", lm("seerr.deleted", title=row.get('title','?')), "deletion")


async def _qbit_apply(torrent_hash: str, qbit_action: str, qbit_tag: str) -> tuple[bool, str, str]:
    """Run the configured action. Returns (ok, ok_message, failure_message)."""
    if qbit_action in ("delete_torrent", "delete_files"):
        # delete_torrent = remove the torrent but KEEP its files ("sans fichiers").
        ok = await qbit_delete_torrent(torrent_hash, delete_files=(qbit_action == "delete_files"))
        return ok, "qbit.torrent_deleted", "qbit.torrent_fail"
    if qbit_action == "pause":
        ok = await qbit_pause_torrent(torrent_hash)
        if ok:
            # Pausing keeps the historical tagging so the torrent stays identifiable.
            ok = await qbit_add_tag(torrent_hash, qbit_tag)
        return ok, "qbit.tag_added", "qbit.tag_fail"
    ok = await qbit_add_tag(torrent_hash, qbit_tag)
    return ok, "qbit.tag_added", "qbit.tag_fail"


async def _handle_qbit(
    torrent_hash: str, title: str, qbit_action: str, qbit_tag: str,
    warnings: Optional[list] = None,
) -> bool:
    """Tag, pause or delete the torrent in qBittorrent based on configured action.

    Returns True on success. Any failure is logged at WARN and appended to
    ``warnings`` (the pipeline's step_warnings) instead of being silent.
    """
    try:
        ok, ok_key, fail_key = await _qbit_apply(torrent_hash, qbit_action, qbit_tag)
        if ok:
            msg = lm(ok_key, title=title, tag=qbit_tag)
            if qbit_action == "delete_torrent":
                msg += " (fichiers conservés)"
            await add_log("INFO", msg, "deletion")
            return True
        await add_log("WARN", lm(fail_key, title=title), "deletion")
        if warnings is not None:
            warnings.append(f"Qbit[{qbit_action}] {torrent_hash}: action failed")
        return False
    except Exception as e:
        await add_log("WARN", lm("qbit.error", title=title, detail=e), "deletion")
        if warnings is not None:
            warnings.append(f"Qbit[{qbit_action}] {torrent_hash}: {e}")
        return False
