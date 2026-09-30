# backend/deletion.py
"""Deletion job: process queue, notify, delete across all services, cleanup."""
import asyncio
import logging
from dataclasses import dataclass
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
from .deletion_helpers import (  # noqa: F401 - re-exported: tests and callers reach these helpers through this module's namespace
    _delete_from_arr, _delete_from_seerr, _find_torrent_hash,
    _find_torrent_hashes_consolidated, _handle_qbit, _is_consolidated_row,
)
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


@dataclass(frozen=True)
class _BatchConfig:
    """Settings read once for a whole deletion batch."""
    dry_run: bool
    run_id: int
    qbit_action: str
    qbit_tag: str
    alert_on_error: bool
    mention: str
    msg_tpl: str
    alert_threshold: int


@dataclass
class _JobOutcome:
    """Final job-run status, written progressively so that the `finally` block
    reports exactly how far the run got (status flips to "success" before the
    closing log and the collection sync, as it always did)."""
    status: str = "error"
    msg: str = ""


async def _load_library_server_map() -> dict:
    """Pre-load library→server_id map (avoids per-item DB queries)."""
    async with get_db() as _lib_db:
        _lib_rows = await _lib_db.fetch_all("SELECT id, server_id FROM libraries")
    return {r["id"]: str(r["server_id"] or "0") for r in _lib_rows}


async def _select_items_to_delete(lib_server_map: dict) -> list:
    """Pending rows that may really be deleted now.

    Last-chance guard before any file is touched: the watch state of the due
    items is re-read from the media server, then anything played since it was
    queued is rescued, never deleted.
    """
    due = await _refresh_watch_state_before_deletion(
        [dict(r) for r in await get_pending_queue()], lib_server_map
    )
    due = await _rescue_watched_since_queued(due)
    return [
        {**dict(r), "_server_id": lib_server_map.get(r.get("library_id"), "0")}
        for r in due
    ]


async def _load_batch_config(dry_run: bool, run_id: int) -> _BatchConfig:
    """Read the qbit and Discord-alert settings once for the whole batch."""
    qbit_action = await get_setting("qbit_action") or "tag_only"
    qbit_tag    = await get_setting("qbit_tag")    or "Supprimé-Hygie"
    alert_on_error = await get_bool_setting("discord_alert_deletion_error")
    mention     = await get_setting("discord_alert_deletion_error_mention") or ""
    msg_tpl     = await get_setting("discord_alert_deletion_error_msg")    or ""
    try:
        threshold = int(await get_setting("discord_alert_error_threshold") or "3")
    except (ValueError, TypeError):
        threshold = 3
    return _BatchConfig(
        dry_run=dry_run, run_id=run_id, qbit_action=qbit_action, qbit_tag=qbit_tag,
        alert_on_error=alert_on_error, mention=mention, msg_tpl=msg_tpl,
        alert_threshold=threshold,
    )


async def _alert_item_failure(row: dict, cfg: _BatchConfig) -> None:
    title = row.get("title", "?")
    await send_alert(
        f"❌ Échec suppression : {title}",
        f"La suppression de **{title}** a échoué.",
        "error",
        mention=cfg.mention, custom_msg=cfg.msg_tpl,
        template_vars={"title": title, "detail": f"Échec suppression de {title}"},
    )


async def _delete_one(
    row: dict, cfg: _BatchConfig, sem: asyncio.Semaphore, counters: dict,
) -> Optional[bool]:
    async with sem:
        # Dry-run simulates only: no claim, no status change.
        if cfg.dry_run:
            return await _delete_media(
                row, True,
                qbit_action=cfg.qbit_action, qbit_tag_val=cfg.qbit_tag, run_id=cfg.run_id,
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
            qbit_action=cfg.qbit_action, qbit_tag_val=cfg.qbit_tag, run_id=cfg.run_id,
        )
        await update_queue_status(row["id"], STATUS_DELETED if ok else STATUS_ERROR)
        if not ok:
            counters["errors"] += 1
            if cfg.alert_on_error:
                await _alert_item_failure(row, cfg)
        return ok


async def _execute_batch(to_delete: list, cfg: _BatchConfig) -> int:
    """Delete the batch (3 at a time), alert past the error threshold.

    Returns the number of items deleted.
    """
    sem      = asyncio.Semaphore(3)
    counters = {"errors": 0}
    results = await asyncio.gather(
        *[_delete_one(r, cfg, sem, counters) for r in to_delete], return_exceptions=True
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
            counters["errors"] += 1

    error_count = counters["errors"]
    if error_count >= cfg.alert_threshold > 0:
        await send_alert(
            f"🚨 {error_count} suppressions en échec",
            f"{error_count} suppressions ont échoué sur {len(to_delete)} tentatives.",
            "error",
            mention=cfg.mention, custom_msg=cfg.msg_tpl,
            template_vars={"count": error_count, "detail": f"{error_count} suppressions en échec"},
        )
    return deleted_count


async def _process_queue(run_id: int, dry_run: bool, outcome: _JobOutcome) -> None:
    """Body of the job, run under the 1-hour cap: notify, select, delete, finalize."""
    await add_log("INFO", lm("deletion.started"), "job")

    # Send threshold notifications (independent per threshold value)
    await _send_pending_notifications()

    lib_server_map = await _load_library_server_map()
    to_delete      = await _select_items_to_delete(lib_server_map)
    cfg            = await _load_batch_config(dry_run, run_id)
    deleted_count  = await _execute_batch(to_delete, cfg)

    prefix         = "[DRY RUN] " if dry_run else ""
    outcome.status = "success"
    outcome.msg    = f"{prefix}{deleted_count} deleted"
    await add_log("INFO", lm("deletion.done", prefix=prefix, n=deleted_count), "job")

    if not dry_run and deleted_count > 0:
        await sync_emby_collection()


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
        run_id    = await add_job_run("deletion_check")
        ctx_token = set_job_context(run_id)   # propagate job_id to all add_log() calls
        outcome   = _JobOutcome()
        dry_run   = await get_bool_setting("dry_run")

        try:
            async with asyncio.timeout(3600):   # 1-hour hard cap
                await _process_queue(run_id, dry_run, outcome)
        except asyncio.TimeoutError:
            logger.error("run_deletion exceeded 1-hour timeout — forcing exit")
            await add_log("ERROR", "Deletion job timeout (1h) — forcibly terminated", "job")
            outcome.status = "error"
            outcome.msg    = "timeout"
        except Exception as e:
            logger.exception("Deletion error")
            await add_log("ERROR", lm("deletion.error", detail=e), "job")
            outcome.msg = str(e)
        finally:
            _current_job_id.reset(ctx_token)
            await finish_job_run(run_id, outcome.status, outcome.msg)


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
