# backend/scanner/_orchestrator.py
"""Top-level scan orchestrator — acquires the scan lock and dispatches per-library work."""
import asyncio
import logging

from .._job_state import _scan_lock
from ..db.engine import get_db
from ..db.settings_store import get_setting, get_bool_setting
from ..db.media_servers import get_media_servers
from ..db.logs import add_job_run, add_log, finish_job_run
from ..db.repositories import get_enabled_libraries, get_queued_and_ignored_ids
from ..emby_client import get_users, ensure_server_uid
from ..arr_clients import (
    build_radarr_path_cache,
    build_seerr_request_cache,
    build_sonarr_path_cache,
)
from ..exceptions import ArrClientError
from ..discord_client import send_alert
from ..notifications import _ensure_notif_columns, _send_pending_notifications
from ..collection import sync_emby_collection
from ..logmsg import lm
from ._emby_scanner import _scan_library
from ._plex_scanner import _scan_plex_library

logger = logging.getLogger(__name__)


async def _purge_stale_seerr_items() -> None:
    """Remove pending queue items that no longer satisfy active expert rules.

    Specifically: if ALL active expert rules for a library require a seerr_user_id
    (have an IN condition on that field), then pending items with seerr_user_id = NULL
    should be removed — they were added before the Seerr filter was configured.
    """
    from ..db.repositories import get_expert_rules
    from ..rules.models import ConditionField, ConditionOp

    try:
        rules = await get_expert_rules(enabled_only=True)
        # Find libraries where every active rule requires a specific seerr_user_id
        # (i.e., at least one condition group has seerr_user_id IN [...])
        libraries_requiring_seerr: set = set()
        for rule in rules:
            has_seerr_filter = any(
                any(
                    c.field == ConditionField.SEERR_USER_ID and c.op == ConditionOp.IN
                    for c in group.conditions
                )
                for group in rule.condition_groups
            )
            if has_seerr_filter:
                if rule.library_ids:
                    for lid in rule.library_ids:
                        libraries_requiring_seerr.add(str(lid))
                elif rule.library_id:
                    libraries_requiring_seerr.add(str(rule.library_id))

        if not libraries_requiring_seerr:
            return

        from ..db.engine import get_db
        async with get_db() as db:
            # Remove pending items with no seerr_user_id in targeted libraries
            placeholders = ",".join("?" * len(libraries_requiring_seerr))
            result = await db.execute_write(
                f"DELETE FROM media_queue WHERE status='pending' "
                f"AND (seerr_user_id IS NULL OR seerr_user_id = 0) "
                f"AND library_id IN ({placeholders})",
                list(libraries_requiring_seerr),
            )
            await db.commit()
            if result:
                await add_log("INFO", lm("scan.seerr_cleanup", n=result), "scan")
    except Exception as e:
        logger.warning("_purge_stale_seerr_items: %s", e)


async def run_scan() -> None:
    """Full scan of all enabled libraries across all enabled servers."""
    if _scan_lock.locked():
        await add_log("WARN", lm("scan.already_running"), "job")
        return

    async with _scan_lock:
        run_id = await add_job_run("scan")
        await add_log("INFO", lm("scan.started"), "job")
        added = 0
        _scan_status, _scan_msg = "error", ""
        await _ensure_notif_columns()
        try:
            servers = await get_media_servers()
            enabled_servers = [s for s in servers if s.get("enabled", True)]
            if not enabled_servers:
                enabled_servers = [{"id": "0", "type": await get_setting("media_server_type") or "emby"}]

            # Build Seerr cache once — Seerr is global, not per-server
            seerr_cache: dict = {}
            try:
                seerr_cache = await build_seerr_request_cache()
            except ArrClientError as _seerr_err:
                await add_log("WARN", lm("scan.seerr_unreachable", detail=_seerr_err), "scan")
                if await get_bool_setting("discord_alert_seerr_failure"):
                    _mention = await get_setting("discord_alert_seerr_failure_mention") or ""
                    _msg     = await get_setting("discord_alert_seerr_failure_msg") or ""
                    await send_alert(
                        "🔌 Seerr inaccessible", str(_seerr_err), "warning",
                        mention=_mention, custom_msg=_msg,
                        template_vars={"detail": str(_seerr_err)},
                    )

            for server in enabled_servers:
                server_id   = str(server.get("id", "0"))
                server_type = server.get("type", "")
                server_name = server.get("name") or ""

                if server_type == "plex":
                    plex_libraries = await get_enabled_libraries(server_id)
                    for lib in plex_libraries:
                        try:
                            n = await _scan_plex_library(server=server, library=lib)
                            added += n
                        except Exception as _pe:
                            await add_log("ERROR", f"Scan Plex {lib['name']}: {_pe}", "scan")
                    continue

                if server_type not in ("emby", "jellyfin", ""):
                    await add_log("INFO", lm("scan.lib_ignored_type", server_id=server_id, type=server_type), "scan")
                    continue

                # Auto-populate server_uid (needed for public calendar links)
                await ensure_server_uid(server_id)

                libraries = await get_enabled_libraries(server_id)
                if not libraries:
                    continue

                users    = await get_users(server_id=server_id)
                user_ids = [u["Id"] for u in users] if users else []

                radarr_cache = await build_radarr_path_cache()
                sonarr_cache = await build_sonarr_path_cache()

                queued_ids, ignored_ids = await get_queued_and_ignored_ids()

                try:
                    max_parallel = int(await get_setting("max_parallel_library_scans") or "3")
                except (ValueError, TypeError):
                    max_parallel = 3
                _lib_sem = asyncio.Semaphore(max(1, max_parallel))

                async def _scan_lib_with_sem(lib):
                    async with _lib_sem:
                        return await _scan_library(
                            lib, user_ids, server_id=server_id, server_name=server_name,
                            radarr_cache=radarr_cache, sonarr_cache=sonarr_cache,
                            seerr_cache=seerr_cache,
                            queued_ids=queued_ids, ignored_ids=ignored_ids,
                        )

                results = await asyncio.gather(
                    *[_scan_lib_with_sem(lib) for lib in libraries],
                    return_exceptions=True,
                )
                for r in results:
                    if isinstance(r, int):
                        added += r
                    elif isinstance(r, Exception):
                        await add_log("ERROR", lm("scan.lib_error", detail=r), "scan")

            await add_log("INFO", lm("scan.done", n=added), "job")
            _scan_status, _scan_msg = "success", f"{added} queued"
            await _purge_stale_seerr_items()
            await sync_emby_collection()
            await _send_pending_notifications()
        except Exception as e:
            logger.exception("Scan error")
            await add_log("ERROR", lm("scan.error", detail=e), "job")
            _scan_msg = str(e)
            if await get_bool_setting("discord_alert_scan_failure"):
                _mention = await get_setting("discord_alert_scan_failure_mention") or ""
                _msg     = await get_setting("discord_alert_scan_failure_msg") or ""
                await send_alert(
                    "🔴 Échec du scan", f"Le scan global a échoué : {e}", "error",
                    mention=_mention, custom_msg=_msg,
                    template_vars={"detail": str(e)},
                )
        finally:
            await finish_job_run(run_id, _scan_status, _scan_msg)


async def run_scan_library(library_id: str) -> None:
    """Scan a single library by ID."""
    if _scan_lock.locked():
        await add_log("WARN", lm("scan.already_running"), "job")
        return

    async with _scan_lock:
        run_id = await add_job_run("scan_library")
        await add_log("INFO", lm("scan.lib_started", id=library_id), "job")
        _sl_status, _sl_msg = "error", ""
        try:
            async with get_db() as db:
                lib = await db.fetch_one(
                    "SELECT * FROM libraries WHERE id=? AND enabled=1",
                    (library_id,),
                )

            if not lib:
                await add_log("WARN", lm("scan.lib_not_found", id=library_id), "scan")
                _sl_status, _sl_msg = "warning", "Library not found"
                return

            server_id = str(lib.get("server_id") or "0")
            # Fetch server name for log messages
            _all_servers = await get_media_servers()
            _srv = next((s for s in _all_servers if str(s.get("id")) == server_id), {})
            server_name = _srv.get("name") or ""

            users     = await get_users(server_id=server_id)
            user_ids  = [u["Id"] for u in users] if users else []

            radarr_cache = await build_radarr_path_cache()
            sonarr_cache = await build_sonarr_path_cache()
            seerr_cache: dict = {}
            try:
                seerr_cache = await build_seerr_request_cache()
            except ArrClientError as _seerr_err:
                await add_log("WARN", f"Seerr inaccessible : {_seerr_err}", "scan")
                if await get_bool_setting("discord_alert_seerr_failure"):
                    _mention = await get_setting("discord_alert_seerr_failure_mention") or ""
                    _msg     = await get_setting("discord_alert_seerr_failure_msg") or ""
                    await send_alert(
                        "🔌 Seerr inaccessible", str(_seerr_err), "warning",
                        mention=_mention, custom_msg=_msg,
                        template_vars={"detail": str(_seerr_err)},
                    )

            async with get_db() as _db:
                _qrows     = await _db.fetch_all("SELECT emby_id FROM media_queue")
                queued_ids = {r["emby_id"] for r in _qrows}
                _irows     = await _db.fetch_all("SELECT emby_id FROM ignored_media")
                ignored_ids = {r["emby_id"] for r in _irows}

            added = await _scan_library(
                lib, user_ids, server_id=server_id, server_name=server_name,
                radarr_cache=radarr_cache, sonarr_cache=sonarr_cache,
                seerr_cache=seerr_cache,
                queued_ids=queued_ids, ignored_ids=ignored_ids,
            )

            await add_log("INFO", lm("scan.done", n=added), "job")
            _sl_status, _sl_msg = "success", f"{added} queued"
            await sync_emby_collection()
            await _send_pending_notifications()
        except Exception as e:
            logger.exception("Scan library error")
            await add_log("ERROR", lm("scan.error", detail=e), "job")
            _sl_msg = str(e)
        finally:
            await finish_job_run(run_id, _sl_status, _sl_msg)
