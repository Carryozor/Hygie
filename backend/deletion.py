# backend/deletion.py
"""Deletion job: process queue, notify, delete across all services, cleanup."""
import asyncio
import logging
from datetime import timedelta
from typing import Optional

from .db.utils import DB_PATH, STATUS_DELETED, STATUS_ERROR, now_utc
from .db.engine import get_db
from .db.settings_store import get_setting, get_bool_setting, get_int_setting
from .db.logs import add_job_run, add_log, finish_job_run
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
from .notifications import _ensure_notif_columns, _send_pending_notifications
from .collection import sync_emby_collection
from ._job_state import _deletion_lock

logger = logging.getLogger(__name__)


# ═══ Deletion ════════════════════════════════════════════════════════════════
async def run_deletion():
    """Process queue: send 30d/7d/1d notifications, delete items past their delete_at."""
    if _deletion_lock.locked():
        await add_log("WARN", "Vérification déjà en cours — ignorée", "job")
        return

    async with _deletion_lock:
        run_id = await add_job_run("deletion_check")
        await add_log("INFO", "Vérification suppressions démarrée", "job")
        dry_run = await get_bool_setting("dry_run")
        deleted_count = 0
        await _ensure_notif_columns()

        try:
            # Send threshold notifications (configurable, independent per threshold)
            await _send_pending_notifications()

            # ── Deletions ────────────────────────────────────────────────────
            to_delete = await get_pending_queue()

            # Read qbit settings once for the whole batch
            _qbit_action = await get_setting("qbit_action") or "tag_only"
            _qbit_tag = await get_setting("qbit_tag") or "Supprimé-Hygie"

            # Semaphore limits concurrent deletions to avoid overwhelming external services
            _del_sem = asyncio.Semaphore(3)
            _alert_del_error = await get_bool_setting("discord_alert_deletion_error")
            _del_mention = await get_setting("discord_alert_deletion_error_mention") or ""
            _del_msg = await get_setting("discord_alert_deletion_error_msg") or ""
            try:
                _alert_threshold = int(await get_setting("discord_alert_error_threshold") or "3")
            except (ValueError, TypeError):
                _alert_threshold = 3
            _error_count = 0

            async def _delete_one(row: dict) -> bool:
                nonlocal _error_count
                async with _del_sem:
                    ok = await _delete_media(row, dry_run, qbit_action=_qbit_action, qbit_tag_val=_qbit_tag, run_id=run_id)
                    status_val = STATUS_DELETED if ok else STATUS_ERROR
                    await update_queue_status(row["id"], status_val)
                    if not ok:
                        _error_count += 1
                        if _alert_del_error:
                            _title_val = row.get("title", "?")
                            await send_alert(
                                f"❌ Échec suppression : {_title_val}",
                                f"La suppression de **{_title_val}** a échoué.",
                                "error",
                                mention=_del_mention,
                                custom_msg=_del_msg,
                                template_vars={"title": _title_val, "detail": f"Échec suppression de {_title_val}"},
                            )
                    return ok

            results = await asyncio.gather(*[_delete_one(r) for r in to_delete], return_exceptions=True)
            deleted_count = sum(1 for r in results if r is True)

            if _error_count >= _alert_threshold and _error_count > 0:
                await send_alert(
                    f"🚨 {_error_count} suppressions en échec",
                    f"{_error_count} suppressions ont échoué sur {len(to_delete)} tentatives dans ce cycle.",
                    "error",
                    mention=_del_mention,
                    custom_msg=_del_msg,
                    template_vars={"count": _error_count, "detail": f"{_error_count} suppressions en échec"},
                )

            prefix = "[DRY RUN] " if dry_run else ""
            await add_log(
                "INFO",
                f"{prefix}Vérification terminée — {deleted_count} suppression(s)",
                "job",
            )
            await finish_job_run(
                run_id,
                "success",
                f"{'[DRY RUN] ' if dry_run else ''}{deleted_count} deleted",
            )

            if not dry_run and deleted_count > 0:
                await sync_emby_collection()
        except Exception as e:
            logger.exception("Deletion error")
            await add_log("ERROR", f"Erreur vérification: {e}", "job")
            await finish_job_run(run_id, "error", str(e))


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
            await add_log("DEBUG", f"Radarr : {title} retiré", "deletion")
        else:
            found = await radarr_find_by_path(file_path)
            if found:
                rid, r_url, r_key = found
                await radarr_delete(int(rid), delete_files=False, url=r_url, key=r_key)
                await add_log("DEBUG", f"Radarr : {title} retiré", "deletion")
    elif sonarr_series_id and season_number is not None:
        # Season-level consolidated entry — try all servers if no cache info
        ok = await sonarr_delete_season(int(sonarr_series_id), int(season_number))
        await add_log("DEBUG" if ok else "WARN", f"Sonarr : {title} saison {season_number} {'retirée' if ok else 'erreur'}", "deletion")
    elif sonarr_series_id:
        # Series-level consolidated entry
        ok = await sonarr_delete_series(int(sonarr_series_id))
        await add_log("DEBUG" if ok else "WARN", f"Sonarr : {title} série {'retirée' if ok else 'erreur'}", "deletion")
    else:
        sid = row.get("sonarr_id") or await sonarr_find_by_path(file_path)
        if sid:
            await sonarr_delete_episode_file(int(sid))
            await add_log("DEBUG", f"Sonarr : {title} retiré", "deletion")


async def _delete_from_seerr(row: dict) -> None:
    """Delete the Seerr request linked to this media, if any."""
    if row.get("seerr_id"):
        await seerr_delete_request(row["seerr_id"])
        await add_log("DEBUG", f"Seerr : requête {row.get('title','?')} supprimée", "deletion")


async def _handle_qbit(torrent_hash: str, title: str, qbit_action: str, qbit_tag: str) -> None:
    """Tag or delete the torrent in qBittorrent based on configured action."""
    try:
        if qbit_action == "delete_torrent":
            ok = await qbit_delete_torrent(torrent_hash, delete_files=True)
            msg = (f"qBittorrent : torrent + fichier supprimés pour {title}"
                   if ok else f"qBittorrent : échec suppression {title}")
            await add_log("INFO" if ok else "WARN", msg, "deletion")
        else:
            ok = await qbit_add_tag(torrent_hash, qbit_tag)
            msg = (f"qBittorrent : tag '{qbit_tag}' ajouté à {title}"
                   if ok else f"qBittorrent : échec tag {title}")
            await add_log("INFO" if ok else "WARN", msg, "deletion")
    except Exception as e:
        await add_log("WARN", f"qBittorrent erreur pour {title}: {e}", "deletion")


async def _get_size_bytes(row: dict) -> int:
    """Best-effort file size lookup from Radarr/Sonarr before deletion."""
    try:
        media_type = row.get("media_type", "")
        if media_type == "Movie":
            rid = row.get("radarr_id")
            if rid:
                movie = await radarr_get(int(rid))
                if movie:
                    return int((movie.get("movieFile") or {}).get("size") or 0)
        else:
            sonarr_series_id = row.get("sonarr_series_id")
            if sonarr_series_id:
                series = await sonarr_get_series_by_id(int(sonarr_series_id))
                if series:
                    return int((series.get("statistics") or {}).get("sizeOnDisk") or 0)
    except Exception as e:
        logger.debug(f"_get_size_bytes: {e}")
    return 0


async def _delete_media(
    row: dict,
    dry_run: bool,
    qbit_action: str = "",
    qbit_tag_val: str = "",
    run_id: int = 0,
) -> bool:
    """
    Delete a media item across all services.

    Order:
      1. Find torrent hash (before removing from arr — history disappears after)
      2. Discord notification (before Emby — image still accessible)
      3. Emby — remove hardlink
      4. Radarr/Sonarr — remove item (keep files)
      5. Seerr — delete request
      6. qBittorrent — tag or delete torrent
    """
    title = row.get("title", "?")
    emby_id = row.get("emby_id")
    dry_prefix = "[DRY RUN] " if dry_run else ""
    job_tag = f"[job:{run_id}] " if run_id else ""
    await add_log("INFO", f"{job_tag}{dry_prefix}Suppression : {title}", "deletion")

    if dry_run:
        return True

    try:
        qbit_action = qbit_action or "tag_only"
        qbit_tag = qbit_tag_val or "Supprimé-Hygie"

        # Resolve library server_id for multi-server correctness
        library_server_id = "0"
        if row.get("library_id"):
            try:
                async with get_db() as _db:
                    lrow = await _db.fetch_one(
                        "SELECT server_id FROM libraries WHERE id=?", (row["library_id"],)
                    )
                    if lrow and lrow["server_id"]:
                        library_server_id = str(lrow["server_id"])
            except Exception as e:
                logger.debug(f"_delete_media: library_server_id lookup: {e}")

        size_bytes, torrent_hash = await asyncio.gather(
            _get_size_bytes(row),
            _find_torrent_hash(row),
        )

        try:
            await send_notification([row], "now", dry_run=False)
        except Exception as e:
            await add_log("WARN", f"{job_tag}Discord (non bloquant) : {e}", "deletion")

        # Resolve server type to route Plex vs Emby/Jellyfin deletion
        _server_type = ""
        try:
            from .db.media_servers import get_media_servers as _gms
            _all_servers = await _gms()
            _srv = next((s for s in _all_servers if str(s.get("id")) == library_server_id), None)
            _server_type = (_srv or {}).get("type", "")
        except Exception:
            pass

        # Skip deletion for consolidated season/series entries (synthetic IDs)
        if emby_id and not str(emby_id).startswith("sonarr-"):
            if _server_type == "plex":
                from .plex_client import build_plex_client as _bpc
                _plex_client = _bpc(_srv or {})
                if _plex_client:
                    rating_key = row.get("plex_rating_key") or emby_id
                    await _plex_client.delete_item(rating_key)
                    await add_log("DEBUG", f"{job_tag}Plex : élément supprimé {rating_key}", "deletion")
            else:
                await delete_item(emby_id, server_id=library_server_id)
                await add_log("DEBUG", f"{job_tag}Emby : hardlink retiré pour {title}", "deletion")

        await _delete_from_arr(row)
        await _delete_from_seerr(row)

        if torrent_hash:
            await _handle_qbit(torrent_hash, title, qbit_action, qbit_tag)
        else:
            await add_log("INFO", f"{job_tag}qBittorrent : torrent non trouvé pour {title} (ignoré)", "deletion")

        await add_log("INFO", f"{job_tag}Suppression complète : {title}", "deletion")

        try:
            month = now_utc().strftime("%Y-%m")
            lib_id = row.get("library_id") or None
            async with get_db() as _db:
                await _db.execute(
                    "INSERT INTO stats_history "
                    "(ts, total_deleted, total_scanned, space_freed_bytes, month, library_id) "
                    "VALUES (?, 1, 0, ?, ?, ?)",
                    (now_utc().isoformat(), size_bytes, month, lib_id),
                )
                await _db.commit()
        except Exception as e:
            logger.debug(f"_delete_media: stats_history insert: {e}")

        return True
    except Exception as e:
        logger.exception(f"_delete_media {title}")
        await add_log("ERROR", f"{job_tag}Erreur suppression {title}: {e}", "deletion")
        return False


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
            await add_log(
                "INFO",
                f"Expiration ignorés : {len(expired)} média(s) remis en circulation",
                "system",
            )

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
                    await add_log(
                        "INFO",
                        f"Rétention : {count} entrée(s) supprimée(s) de l'historique (>{retention_days}j)",
                        "system",
                    )
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
                    await add_log(
                        "INFO",
                        f"Purge logs : {count} entrée(s) > {log_retention}j supprimée(s)",
                        "system",
                    )
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

    # VACUUM + WAL checkpoint to reclaim disk space after large purges
    if purged_rows > 1000:
        try:
            loop = asyncio.get_running_loop()
            import sqlite3 as _sqlite3
            def _vacuum():
                conn = _sqlite3.connect(DB_PATH, timeout=30)
                conn.execute("VACUUM")
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.close()
            await loop.run_in_executor(None, _vacuum)
            await add_log("INFO", f"VACUUM exécuté — {purged_rows} entrées purgées, espace disque libéré", "system")
        except Exception as e:
            logger.debug(f"VACUUM: {e}")


async def _delete_single_item(*, item: dict, server: dict, dry_run: bool = False) -> bool:
    """Delete one item via the appropriate client based on server type.

    Thin wrapper used for testing and manual single-item deletion.
    For Plex servers, uses PlexClient.delete_item().
    For Emby/Jellyfin servers, calls emby_client.delete_item().
    """
    if server.get("type") == "plex":
        from .plex_client import build_plex_client
        plex = build_plex_client(server)
        if plex is None:
            return False
        rating_key = item.get("plex_rating_key") or item.get("emby_id", "")
        if dry_run:
            logger.info("[DRY RUN] Plex: would delete ratingKey=%s", rating_key)
            return True
        return await plex.delete_item(rating_key)
    else:
        emby_id = item.get("emby_id", "")
        server_id = str(server.get("id", "0"))
        if dry_run:
            logger.info("[DRY RUN] Emby: would delete emby_id=%s", emby_id)
            return True
        await delete_item(emby_id, server_id=server_id)
        return True
