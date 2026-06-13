# backend/deletion.py
"""Deletion job: process queue, notify, delete across all services, cleanup."""
import asyncio
import logging
from datetime import timedelta
from typing import Optional

from .db.utils import (
    DB_PATH, STATUS_DELETED, STATUS_DELETING, STATUS_ERROR, STATUS_PENDING, now_utc,
)
from .db.engine import get_db
from .db.settings_store import get_setting, get_bool_setting, get_int_setting
from .db.logs import add_job_run, add_log, finish_job_run, set_job_context, _current_job_id
from .db.repositories import get_pending_queue, update_queue_status
from .emby_client import delete_item, get_client
from .arr_clients import (
    radarr_delete, radarr_find_by_path, radarr_get, radarr_get_torrent_hash,
    radarr_delete_by_id, radarr_get_torrent_hash_any,
    seerr_delete_request, sonarr_delete_episode_file, sonarr_delete_season,
    sonarr_delete_series, sonarr_find_by_path, sonarr_get_series_by_id,
    sonarr_get_torrent_hash,
)
from .qbit_client import qbit_add_tag, qbit_delete_torrent, qbit_find_by_path
from .discord_client import send_alert, send_notification
from .notifications import _send_pending_notifications
from .collection import sync_emby_collection
from ._job_state import _deletion_lock
from .logmsg import lm

logger = logging.getLogger(__name__)


# ═══ Deletion ════════════════════════════════════════════════════════════════

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

                to_delete = [
                    {**dict(r), "_server_id": _lib_server_map.get(r.get("library_id"), "0")}
                    for r in await get_pending_queue()
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


async def reset_stale_deleting() -> int:
    """Recover items stuck in 'deleting' after a crash mid-deletion.

    Called at startup: with a single process, any 'deleting' row at boot is
    stale by definition (the claim holder died before finishing). Without this
    reset the items are invisible to get_pending_queue() forever.
    """
    async with get_db() as db:
        n = await db.execute_write(
            "UPDATE media_queue SET status=? WHERE status=?",
            (STATUS_PENDING, STATUS_DELETING),
        )
        await db.commit()
    if n:
        logger.warning("Recovered %d item(s) stuck in 'deleting' status (crash recovery)", n)
    return n


async def _claim_pending(item_id: int) -> bool:
    """Atomically claim a queue item (pending → deleting).

    Returns False if the item was already claimed (e.g. by the delete-now
    endpoint) — the caller must then skip it to avoid a double deletion.
    """
    async with get_db() as db:
        claimed = await db.execute_write(
            "UPDATE media_queue SET status=? WHERE id=? AND status=?",
            (STATUS_DELETING, item_id, STATUS_PENDING),
        )
        await db.commit()
    return bool(claimed)


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


async def _delete_from_arr(row: dict) -> None:
    """Remove media from Radarr or Sonarr."""
    file_path = row.get("file_path", "")
    media_type = row.get("media_type", "")
    title = row.get("title", "?")
    sonarr_series_id = row.get("sonarr_series_id")
    season_number = row.get("season_number")

    if media_type == "Movie":
        rid_stored = row.get("radarr_id")
        if rid_stored:
            await radarr_delete_by_id(int(rid_stored), delete_files=False)
            await add_log("DEBUG", lm("radarr.removed", title=title), "deletion")
        else:
            found = await radarr_find_by_path(file_path)
            if found:
                rid, r_url, r_key = found
                await radarr_delete(int(rid), delete_files=False, url=r_url, key=r_key)
                await add_log("DEBUG", lm("radarr.removed", title=title), "deletion")
    elif sonarr_series_id and season_number is not None:
        # Season-level consolidated entry — try all servers if no cache info
        ok = await sonarr_delete_season(int(sonarr_series_id), int(season_number))
        await add_log("DEBUG" if ok else "WARN", lm("sonarr.season_ok" if ok else "sonarr.season_err", title=title, n=season_number), "deletion")
    elif sonarr_series_id:
        # Series-level consolidated entry
        ok = await sonarr_delete_series(int(sonarr_series_id))
        await add_log("DEBUG" if ok else "WARN", lm("sonarr.series_ok" if ok else "sonarr.series_err", title=title), "deletion")
    else:
        sid = row.get("sonarr_id") or await sonarr_find_by_path(file_path)
        if sid:
            await sonarr_delete_episode_file(int(sid))
            await add_log("DEBUG", lm("sonarr.removed", title=title), "deletion")


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
            async with get_db() as db:
                row = await db.fetch_one(
                    "SELECT COUNT(*) AS cnt FROM media_queue "
                    "WHERE status='deleted' AND detected_at < ?",
                    (cutoff,),
                )
                count = row["cnt"] if row else 0
                if count:
                    await db.execute(
                        "DELETE FROM media_queue WHERE status='deleted' AND detected_at < ?",
                        (cutoff,),
                    )
                    await db.commit()
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
