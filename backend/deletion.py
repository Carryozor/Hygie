# backend/deletion.py
"""Deletion job: process queue, notify, delete across all services, cleanup."""
import asyncio
import logging
from datetime import timedelta
from typing import Optional

from .db.utils import (
    DB_PATH, STATUS_DELETED, STATUS_ERROR, now_utc, parse_iso_dt,
)
from .db.engine import get_db
from .db.settings_store import get_setting, get_bool_setting, get_int_setting
from .db.logs import add_job_run, add_log, finish_job_run, set_job_context, _current_job_id
from .emby_client import delete_item, get_client, get_play_activity  # noqa: F401 - delete_item is unused in this module's own code, but mock.patch("backend.deletion.delete_item") targets require it re-imported here
from .db.repositories import (
    get_pending_queue, update_queue_status,
    reset_deleting_to_pending, claim_for_deletion,
    delete_stale_deleted, delete_by_id,
    update_activity_log_batch, update_consolidated_watch_state,
)
from .arr_clients import (
    radarr_delete, radarr_find_by_path, radarr_get_torrent_hash,
    radarr_delete_by_id, radarr_get_torrent_hash_any,
    seerr_delete_request, sonarr_delete_episode_file, sonarr_delete_season,
    sonarr_delete_series, sonarr_find_by_path, sonarr_get_torrent_hash,
)
from .qbit_client import qbit_add_tag, qbit_delete_torrent, qbit_find_by_path
from .discord_client import send_alert, send_notification  # noqa: F401 - send_notification unused directly, but mock.patch("backend.deletion.send_notification") targets require it re-imported here
from .notifications import _send_pending_notifications
from .collection import sync_emby_collection
from ._job_state import _deletion_lock
from ._lock_backend import LockNotAvailable
from ._job_health import warn_if_job_starved
from .logmsg import lm

logger = logging.getLogger(__name__)


# ═══ Deletion ════════════════════════════════════════════════════════════════

_CONSOLIDATED_PREFIXES = ("sonarr-series:", "sonarr-season:")


def _advance_last_played(row: dict, played: str) -> None:
    """Move a row's last_played forward, never backward.

    ISO strings are compared as datetimes: a raw string comparison would be
    wrong across differing UTC offsets.
    """
    current = parse_iso_dt(row.get("last_played"))
    fresh   = parse_iso_dt(played)
    if fresh is not None and (current is None or fresh > current):
        row["last_played"] = played


async def _consolidated_last_play(row: dict, server_id: str, activity: dict) -> Optional[str]:
    """Most recent play among the episodes a consolidated row stands for.

    Consolidated rows carry a synthetic emby_id, so they never appear in the
    activity log themselves. Resolve the Series/Season library item from its
    on-disk path — the same resolution the deletion pipeline already performs —
    then look its episodes up in the activity log.
    """
    import os

    from .arr_clients import sonarr_get_series_by_id_any
    from .emby_client import find_item_by_path, get_items_in_library

    series_id = row.get("sonarr_series_id")
    if not series_id:
        return None
    series      = await sonarr_get_series_by_id_any(int(series_id))
    series_path = (series or {}).get("path") or ""
    if row.get("season_number") is not None:
        season_path = os.path.dirname(row.get("file_path") or "")
        target = await find_item_by_path(season_path, include_types="Season", server_id=server_id) if season_path else None
    else:
        target = await find_item_by_path(series_path, include_types="Series", server_id=server_id) if series_path else None
    if not target:
        return None

    episodes, _ = await get_items_in_library(str(target.get("Id")), limit=500, start=0, server_id=server_id)
    plays = [activity[str(ep.get("Id"))] for ep in episodes if str(ep.get("Id")) in activity]
    return max(plays) if plays else None


async def _refresh_watch_state_before_deletion(rows: list, lib_server_map: dict) -> list:
    """Refresh last_played for the items due for deletion, from the media server.

    The DB's last_played is only as fresh as the last scan (6 h by default) while
    the deletion job runs hourly — a play in between is invisible to the rescue
    guard below. Only the due rows are refreshed, so this costs one activity-log
    fetch per server plus two lookups per consolidated row, not a library sweep.

    A server whose watch state cannot be read has its rows dropped from this
    batch: deleting on unknown watch state is what caused incident 2026-09-15.
    They stay queued and the next run retries.
    """
    by_server: dict = {}
    for row in rows:
        by_server.setdefault(lib_server_map.get(row.get("library_id"), "0"), []).append(row)

    keep: list = []
    for server_id, server_rows in by_server.items():
        url, key = await get_client(server_id)
        if not url or not key:
            # Nothing to read from (unconfigured or Plex server): keep whatever
            # the last scan wrote — the guard below still applies to it.
            keep.extend(server_rows)
            continue
        try:
            activity = await get_play_activity(server_id=server_id, days=90)
        except Exception as e:
            logger.warning(
                "Deletion postponed for %d item(s) on server %s — watch state "
                "unavailable: %s", len(server_rows), server_id, e,
            )
            await add_log(
                "WARN",
                f"Suppression reportée pour {len(server_rows)} média(s) : "
                "historique de visionnage indisponible",
                "deletion",
            )
            continue

        direct: list = []
        consolidated: list = []
        for row in server_rows:
            emby_id = row.get("emby_id") or ""
            if emby_id.startswith(_CONSOLIDATED_PREFIXES):
                played = await _consolidated_last_play(row, server_id, activity)
                if played:
                    consolidated.append(
                        (played, max(int(row.get("view_count") or 0), 1), emby_id, played)
                    )
                    _advance_last_played(row, played)
            else:
                played = activity.get(emby_id)
                if played:
                    direct.append((played, emby_id, played))
                    _advance_last_played(row, played)
            keep.append(row)

        try:
            await update_activity_log_batch(direct)
            await update_consolidated_watch_state(consolidated)
        except Exception as e:
            logger.warning("Pre-deletion watch-state write failed: %s", e)

    return keep


async def _rescue_watched_since_queued(rows: list) -> list:
    """Drop from the deletion batch every item played after it was queued.

    Last-chance guard. Queueing happens once, at scan time; nothing afterwards
    re-evaluates a pending row, so a media someone started watching during the
    grace period was still deleted on its delete_at (incident 2026-09-15). A
    play recorded after `detected_at` is unambiguous evidence the item is in
    use: it is removed from the queue and left alone. The next scan re-queues it
    only if it genuinely still matches the library's conditions.

    Returns the rows that may proceed to deletion.
    """
    keep: list = []
    for row in rows:
        detected = parse_iso_dt(row.get("detected_at"))
        played   = parse_iso_dt(row.get("last_played"))
        if detected is None or played is None or played <= detected:
            keep.append(row)
            continue
        title = row.get("title", "?")
        await add_log(
            "INFO",
            f"Suppression annulée : « {title} » a été vu le "
            f"{played.strftime('%d/%m/%Y')}, après sa mise en file — retiré de la file",
            "deletion",
        )
        try:
            await delete_by_id(row["id"])
        except Exception as e:
            logger.warning("Rescue of '%s' failed to clear the queue row: %s", title, e)
    return keep


async def run_deletion() -> None:
    """Process queue: send threshold notifications, delete items past their delete_at.

    Hard timeout: 1 hour. If the job exceeds this limit it is likely stuck waiting
    on an unresponsive external service — the timeout forces a clean exit so the
    next scheduled run can start fresh.
    """
    if _deletion_lock.locked():
        await add_log("WARN", lm("deletion.already_running"), "job")
        return

    async with _deletion_lock:
        run_id      = await add_job_run("deletion_check")
        ctx_token   = set_job_context(run_id)   # propagate job_id to all add_log() calls
        _dl_status  = "error"
        _dl_msg     = ""
        deleted_count = 0
        dry_run     = await get_bool_setting("dry_run")

        try:
            async with asyncio.timeout(3600):   # 1-hour hard cap
                await add_log("INFO", lm("deletion.started"), "job")

                # Send threshold notifications (independent per threshold value)
                await _send_pending_notifications()

                # Pre-load library→server_id map (avoids per-item DB queries)
                async with get_db() as _lib_db:
                    _lib_rows = await _lib_db.fetch_all("SELECT id, server_id FROM libraries")
                _lib_server_map = {r["id"]: str(r["server_id"] or "0") for r in _lib_rows}

                # Last-chance guard before any file is touched: the watch state
                # of the due items is re-read from the media server, then
                # anything played since it was queued is rescued, never deleted.
                _due = await _refresh_watch_state_before_deletion(
                    [dict(r) for r in await get_pending_queue()], _lib_server_map
                )
                _due = await _rescue_watched_since_queued(_due)
                to_delete = [
                    {**dict(r), "_server_id": _lib_server_map.get(r.get("library_id"), "0")}
                    for r in _due
                ]

                # Read qbit settings once for the whole batch
                _qbit_action = await get_setting("qbit_action") or "tag_only"
                _qbit_tag    = await get_setting("qbit_tag")    or "Supprimé-Hygie"

                _del_sem         = asyncio.Semaphore(3)
                _alert_del_error = await get_bool_setting("discord_alert_deletion_error")
                _del_mention     = await get_setting("discord_alert_deletion_error_mention") or ""
                _del_msg_tpl     = await get_setting("discord_alert_deletion_error_msg")    or ""
                try:
                    _alert_threshold = int(await get_setting("discord_alert_error_threshold") or "3")
                except (ValueError, TypeError):
                    _alert_threshold = 3
                _counters = {"errors": 0}

                async def _delete_one(row: dict) -> Optional[bool]:
                    async with _del_sem:
                        # Dry-run simulates only: no claim, no status change.
                        if dry_run:
                            return await _delete_media(
                                row, True,
                                qbit_action=_qbit_action, qbit_tag_val=_qbit_tag, run_id=run_id,
                            )
                        # Atomic claim (pending → deleting) — the same item may be
                        # processed concurrently via the delete-now endpoint.
                        if not await _claim_pending(row["id"]):
                            logger.info(
                                "Deletion: item %s already claimed elsewhere — skipping",
                                row.get("title", row["id"]),
                            )
                            return None
                        ok = await _delete_media(
                            row, False,
                            qbit_action=_qbit_action, qbit_tag_val=_qbit_tag, run_id=run_id,
                        )
                        await update_queue_status(row["id"], STATUS_DELETED if ok else STATUS_ERROR)
                        if not ok:
                            _counters["errors"] += 1
                            if _alert_del_error:
                                _t = row.get("title", "?")
                                await send_alert(
                                    f"❌ Échec suppression : {_t}",
                                    f"La suppression de **{_t}** a échoué.",
                                    "error",
                                    mention=_del_mention, custom_msg=_del_msg_tpl,
                                    template_vars={"title": _t, "detail": f"Échec suppression de {_t}"},
                                )
                        return ok

                results = await asyncio.gather(
                    *[_delete_one(r) for r in to_delete], return_exceptions=True
                )
                deleted_count = sum(1 for r in results if r is True)
                # _delete_one() catches its own per-item errors and returns False/None —
                # an Exception surfacing here means something escaped that handling
                # (e.g. a DB error in _claim_pending). Log it instead of dropping it
                # silently, so a systemic bug doesn't just look like "0 deleted".
                for r, item in zip(results, to_delete):
                    if isinstance(r, Exception):
                        logger.error(
                            "Deletion: unhandled exception for '%s': %s",
                            item.get("title", item.get("id")), r,
                        )
                        _counters["errors"] += 1

                _error_count = _counters["errors"]
                if _error_count >= _alert_threshold > 0:
                    await send_alert(
                        f"🚨 {_error_count} suppressions en échec",
                        f"{_error_count} suppressions ont échoué sur {len(to_delete)} tentatives.",
                        "error",
                        mention=_del_mention, custom_msg=_del_msg_tpl,
                        template_vars={"count": _error_count, "detail": f"{_error_count} suppressions en échec"},
                    )

                prefix   = "[DRY RUN] " if dry_run else ""
                _dl_status = "success"
                _dl_msg    = f"{prefix}{deleted_count} deleted"
                await add_log("INFO", lm("deletion.done", prefix=prefix, n=deleted_count), "job")

                if not dry_run and deleted_count > 0:
                    await sync_emby_collection()

        except asyncio.TimeoutError:
            logger.error("run_deletion exceeded 1-hour timeout — forcing exit")
            await add_log("ERROR", "Deletion job timeout (1h) — forcibly terminated", "job")
            _dl_status = "error"
            _dl_msg    = "timeout"
        except Exception as e:
            logger.exception("Deletion error")
            await add_log("ERROR", lm("deletion.error", detail=e), "job")
            _dl_msg = str(e)
        finally:
            _current_job_id.reset(ctx_token)
            await finish_job_run(run_id, _dl_status, _dl_msg)


async def _run_deletion_guarded() -> None:
    """Thin wrapper that silences LockNotAvailable in multi-worker mode."""
    try:
        await run_deletion()
    except LockNotAvailable:
        await warn_if_job_starved(
            "deletion_check", "deletion_check_interval_minutes", 60,
            "Vérification des suppressions",
        )
        logger.debug("run_deletion: another worker holds the deletion lock — skipping")


async def reset_stale_deleting() -> int:
    """Recover items stuck in 'deleting' after a crash mid-deletion."""
    n = await reset_deleting_to_pending()
    if n:
        logger.warning("Recovered %d item(s) stuck in 'deleting' status (crash recovery)", n)
    return n


async def _claim_pending(item_id: int) -> bool:
    """Atomically claim a queue item (pending → deleting)."""
    return await claim_for_deletion(item_id)


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
    consolidated = _is_consolidated_row(row)

    if media_type == "Movie":
        rid_stored = row.get("radarr_id")
        if rid_stored:
            ok = await radarr_delete_by_id(int(rid_stored), delete_files=False)
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
        # Season-level consolidated entry — try all servers if no cache info
        ok = await sonarr_delete_season(int(sonarr_series_id), int(season_number))
        await add_log("DEBUG" if ok else "WARN", lm("sonarr.season_ok" if ok else "sonarr.season_err", title=title, n=season_number), "deletion")
        return ok
    elif consolidated:
        # Series-level consolidated entry
        ok = await sonarr_delete_series(int(sonarr_series_id))
        await add_log("DEBUG" if ok else "WARN", lm("sonarr.series_ok" if ok else "sonarr.series_err", title=title), "deletion")
        return ok
    else:
        sid = row.get("sonarr_id") or await sonarr_find_by_path(file_path)
        if sid:
            ok = await sonarr_delete_episode_file(int(sid))
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


async def _delete_media(
    row: dict,
    dry_run: bool,
    qbit_action: str = "tag_only",
    qbit_tag_val: str = "Supprimé-Hygie",
    run_id: int = 0,
) -> bool:
    """Delete a media item across all services using the DeletionPipeline.

    The pipeline handles: size lookup, torrent hash, Discord notification,
    Emby/Plex deletion, Radarr/Sonarr removal, Seerr cleanup, qBit tagging,
    and stats recording — in the correct order.
    """
    from .deletion_pipeline import DeletionContext, build_default_pipeline

    title      = row.get("title", "?")
    job_tag    = f"[job:{run_id}] " if run_id else ""
    dry_prefix = "[DRY RUN] " if dry_run else ""
    log_prefix = job_tag + dry_prefix

    await add_log("INFO", lm("deletion.item_start", prefix=log_prefix, title=title), "deletion")

    if dry_run:
        return True

    ctx = DeletionContext(
        item=dict(row),
        dry_run=False,
        run_id=run_id,
        qbit_action=qbit_action or "tag_only",
        qbit_tag=qbit_tag_val or "Supprimé-Hygie",
    )

    ok = await build_default_pipeline().execute(ctx)

    if ok:
        await add_log("INFO", lm("deletion.item_done", prefix=log_prefix, title=title), "deletion")
    else:
        await add_log("ERROR", lm("deletion.item_error", prefix=log_prefix, title=title, detail="pipeline step failed"), "deletion")

    return ok


# ═══ Ignored cleanup ══════════════════════════════════════════════════════════
async def run_ignored_cleanup():
    """Remove expired ignored_media entries + purge old deleted queue entries + rotate logs."""
    now = now_utc().isoformat()
    purged_rows = 0

    # Expire ignored items
    async with get_db() as db:
        expired = await db.fetch_all(
            "SELECT title FROM ignored_media "
            "WHERE expire_at IS NOT NULL AND expire_at <= ?",
            (now,),
        )
        if expired:
            await db.execute(
                "DELETE FROM ignored_media WHERE expire_at IS NOT NULL AND expire_at <= ?",
                (now,),
            )
            await db.commit()
            await add_log("INFO", lm("cleanup.ignored_expired", n=len(expired)), "system")

    # Purge deleted entries older than retention setting
    try:
        retention_days = await get_int_setting("deleted_retention_days", 90)
        if retention_days > 0:
            cutoff = (now_utc() - timedelta(days=retention_days)).isoformat()
            count = await delete_stale_deleted(cutoff)
            if count:
                purged_rows += count
                await add_log("INFO", lm("cleanup.retention", n=count, days=retention_days), "system")
    except Exception as e:
        logger.debug(f"Purge retention: {e}")

    # Purge old log entries
    try:
        log_retention = await get_int_setting("log_retention_days", 14)
        if log_retention > 0:
            cutoff = (now_utc() - timedelta(days=log_retention)).isoformat()
            async with get_db() as db:
                row = await db.fetch_one(
                    "SELECT COUNT(*) AS cnt FROM logs WHERE ts < ?", (cutoff,)
                )
                count = row["cnt"] if row else 0
                if count:
                    await db.execute("DELETE FROM logs WHERE ts < ?", (cutoff,))
                    await db.commit()
                    purged_rows += count
                    await add_log("INFO", lm("cleanup.logs", n=count, days=log_retention), "system")
    except Exception as e:
        logger.debug(f"Purge logs: {e}")

    # Purge old job_history entries
    try:
        jh_retention = await get_int_setting("job_history_retention_days", 90)
        if jh_retention > 0:
            cutoff = (now_utc() - timedelta(days=jh_retention)).isoformat()
            async with get_db() as db:
                row = await db.fetch_one(
                    "SELECT COUNT(*) AS cnt FROM job_history WHERE started_at < ?", (cutoff,)
                )
                count = row["cnt"] if row else 0
                if count:
                    await db.execute(
                        "DELETE FROM job_history WHERE started_at < ?", (cutoff,)
                    )
                    await db.commit()
                    purged_rows += count
    except Exception as e:
        logger.debug(f"Purge job_history: {e}")

    # VACUUM + WAL checkpoint — SQLite only (no equivalent on MariaDB)
    if purged_rows > 1000:
        from .db.engine import DIALECT
        if DIALECT == "sqlite":
            try:
                loop = asyncio.get_running_loop()
                import sqlite3 as _sqlite3
                def _vacuum():
                    conn = _sqlite3.connect(DB_PATH, timeout=30)
                    conn.execute("VACUUM")
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    conn.close()
                await loop.run_in_executor(None, _vacuum)
                await add_log("INFO", lm("cleanup.vacuum", n=purged_rows), "system")
            except Exception as e:
                logger.debug(f"VACUUM: {e}")
        else:
            logger.debug("VACUUM/WAL checkpoint skipped (dialect: %s)", DIALECT)


async def _delete_single_item(*, item: dict, server: dict, dry_run: bool = False) -> bool:
    """Delete one item via the appropriate client based on server type."""
    from .media_server_factory import delete_server_item, get_server_item_id
    from .db.media_servers import is_plex
    if dry_run:
        item_id = get_server_item_id(server, item)
        label = "ratingKey" if is_plex(server) else "emby_id"
        logger.info("[DRY RUN] %s: would delete %s=%s", server.get("type", "emby"), label, item_id)
        return True
    return await delete_server_item(server, item)
