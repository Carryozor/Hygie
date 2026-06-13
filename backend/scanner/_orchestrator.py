# backend/scanner/_orchestrator.py
"""Top-level scan orchestrator — acquires the scan lock and dispatches per-library work."""
import asyncio
import logging

from .._job_state import _scan_lock
from ..db.engine import get_db
from ..db.settings_store import get_setting, get_bool_setting
from ..db.media_servers import get_media_servers
from ..db.logs import add_job_run, add_log, finish_job_run, set_job_context, _current_job_id
from ..db.repositories import get_enabled_libraries, get_queued_and_ignored_ids
from ..emby_client import get_users, ensure_server_uid
from ..arr_clients import (
    build_radarr_path_cache,
    build_seerr_request_cache,
    build_sonarr_path_cache,
)
from ..exceptions import ArrClientError
from ..discord_client import send_alert
from ..notifications import _send_pending_notifications
from ..collection import sync_emby_collection
from ..logmsg import lm
from ..emby_client import get_play_activity
from ._emby_scanner import _scan_library
from ._plex_scanner import _scan_plex_library

logger = logging.getLogger(__name__)


async def _purge_stale_seerr_items() -> None:
    """Remove pending queue items that no longer satisfy active expert rules.

    Specifically: if ALL active expert rules for a library require a seerr_user_id
    (have an IN condition on that field), then pending items with seerr_user_id = NULL
    should be removed — they were added before the Seerr filter was configured.
    """
    from collections import defaultdict
    from ..db.repositories import get_expert_rules
    from ..rules.models import ConditionField, ConditionOp

    def _has_seerr_filter(rule) -> bool:
        return any(
            any(
                c.field == ConditionField.SEERR_USER_ID and c.op == ConditionOp.IN
                for c in group.conditions
            )
            for group in rule.condition_groups
        )

    try:
        rules = await get_expert_rules(enabled_only=True)

        # Group rules by the library IDs they target. Rules with no library
        # scope are global and apply to every library.
        library_to_rules: dict[str, list] = defaultdict(list)
        global_rules: list = []
        for rule in rules:
            if rule.library_ids:
                for lid in rule.library_ids:
                    library_to_rules[str(lid)].append(rule)
            elif rule.library_id:
                library_to_rules[str(rule.library_id)].append(rule)
            else:
                global_rules.append(rule)

        # A library qualifies for purge only when EVERY rule that can fire on
        # it (library-specific + global) requires a seerr_user_id. If even one
        # rule lacks a Seerr filter, items without seerr_user_id may be
        # legitimately queued by that rule.
        libraries_requiring_seerr: set = set()
        for lid, lib_rules in library_to_rules.items():
            all_applicable = lib_rules + global_rules
            if all_applicable and all(_has_seerr_filter(r) for r in all_applicable):
                libraries_requiring_seerr.add(lid)

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


async def _do_scan_one_library(
    library_id: str,
    *,
    seerr_cache: dict | None = None,
    radarr_cache: dict | None = None,
    sonarr_cache: dict | None = None,
) -> tuple[str, str, int]:
    """Scan a single library. Returns (status, msg, added_count).

    Optional pre-built caches avoid redundant API calls when scanning multiple
    libraries in sequence (run_scan_libraries builds them once and passes them in).
    When None, each cache is built locally inside this function.
    """
    async with get_db() as db:
        lib = await db.fetch_one(
            "SELECT * FROM libraries WHERE id=? AND enabled=1", (library_id,)
        )

    if not lib:
        await add_log("WARN", lm("scan.lib_not_found", id=library_id), "scan")
        return "warning", "Library not found", 0

    lib_name  = lib.get("name") or library_id
    server_id = str(lib.get("server_id") or "0")

    _all_servers = await get_media_servers()
    _srv = next((s for s in _all_servers if str(s.get("id")) == server_id), {})
    server_name = _srv.get("name") or ""
    server_type = _srv.get("type", "")

    # Skip libraries whose server is disabled — avoids re-queuing items for an
    # inactive server when a rule triggers a targeted scan on specific libraries.
    if _srv and _srv.get("enabled") is False:
        await add_log("INFO", f"Scan ignoré : serveur '{server_name}' désactivé", "scan")
        return "skipped", f"server {server_name!r} disabled", 0

    await add_log("INFO", lm("scan.lib_started", id=lib_name), "job")

    # Build Seerr cache if caller did not provide one
    if seerr_cache is None:
        seerr_cache = {}
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

    # ── Plex path ────────────────────────────────────────────────────────────
    if server_type == "plex":
        added = await _scan_plex_library(server=_srv, library=lib, seerr_cache=seerr_cache)
        await add_log("INFO", lm("scan.done", n=added), "job")
        return "success", f"{added} queued", added

    # ── Emby / Jellyfin path ─────────────────────────────────────────────────
    users    = await get_users(server_id=server_id)
    user_ids = [u["Id"] for u in users] if users else []

    if radarr_cache is None:
        radarr_cache = await build_radarr_path_cache()
    if sonarr_cache is None:
        sonarr_cache = await build_sonarr_path_cache()

    async with get_db() as _db:
        _qrows      = await _db.fetch_all("SELECT emby_id FROM media_queue")
        queued_ids  = {r["emby_id"] for r in _qrows}
        _irows      = await _db.fetch_all("SELECT emby_id FROM ignored_media")
        ignored_ids = {r["emby_id"] for r in _irows}

    # Fetch activity log once per single-library scan (same as per-server optimization
    # in _run_scan_body). Passing activity_log=None would cause _scan_library to fetch
    # it internally, which is equivalent but less explicit.
    _activity_log: dict = {}
    try:
        _activity_log = await get_play_activity(server_id=server_id, days=730)
    except Exception as _al_e:
        logger.warning("activity log fetch failed for server %s: %s", server_id, _al_e)

    added = await _scan_library(
        lib, user_ids, server_id=server_id, server_name=server_name,
        radarr_cache=radarr_cache, sonarr_cache=sonarr_cache,
        seerr_cache=seerr_cache,
        queued_ids=queued_ids, ignored_ids=ignored_ids,
        activity_log=_activity_log,
    )
    await add_log("INFO", lm("scan.done", n=added), "job")
    return "success", f"{added} queued", added


async def _scan_single_server(
    server: dict,
    *,
    radarr_cache: dict,
    sonarr_cache: dict,
    seerr_cache: dict,
) -> int:
    """Scan one media server — Plex or Emby/Jellyfin. Returns count of items queued."""
    server_id   = str(server.get("id", "0"))
    server_type = server.get("type", "")
    server_name = server.get("name") or ""
    added = 0

    if server_type == "plex":
        plex_libraries = await get_enabled_libraries(server_id)
        for lib in plex_libraries:
            try:
                n = await _scan_plex_library(server=server, library=lib, seerr_cache=seerr_cache)
                added += n
            except Exception as _pe:
                await add_log("ERROR", f"Scan Plex {lib['name']}: {_pe}", "scan")
        return added

    if server_type not in ("emby", "jellyfin", ""):
        await add_log("INFO", lm("scan.lib_ignored_type", server_id=server_id, type=server_type), "scan")
        return 0

    await ensure_server_uid(server_id)
    libraries = await get_enabled_libraries(server_id)
    if not libraries:
        return 0

    users    = await get_users(server_id=server_id)
    user_ids = [u["Id"] for u in users] if users else []
    queued_ids, ignored_ids = await get_queued_and_ignored_ids()

    server_activity_log: dict = {}
    try:
        server_activity_log = await get_play_activity(server_id=server_id, days=730)
    except Exception as _al_err:
        logger.warning("activity log fetch failed for server %s: %s", server_id, _al_err)

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
                activity_log=server_activity_log,
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
    return added


async def _build_shared_caches() -> tuple[dict, dict, dict]:
    """Build Seerr/Radarr/Sonarr caches once for a full scan run.

    Returns (seerr_cache, radarr_cache, sonarr_cache). On Seerr failure the
    seerr_cache is empty and a WARN log + optional Discord alert is sent.
    Radarr/Sonarr failures propagate as exceptions to the caller.
    """
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
    radarr_cache = await build_radarr_path_cache()
    sonarr_cache = await build_sonarr_path_cache()
    return seerr_cache, radarr_cache, sonarr_cache


async def _run_scan_body(run_id: int) -> tuple[str, str]:
    """Inner scan logic — separated so run_scan() can wrap it with a timeout."""
    await add_log("INFO", lm("scan.started"), "job")
    added = 0
    try:
        servers = await get_media_servers()
        enabled_servers = [s for s in servers if s.get("enabled", True)]
        if not enabled_servers:
            enabled_servers = [{"id": "0", "type": await get_setting("media_server_type") or "emby"}]

        seerr_cache, radarr_cache, sonarr_cache = await _build_shared_caches()

        for server in enabled_servers:
            added += await _scan_single_server(
                server,
                radarr_cache=radarr_cache,
                sonarr_cache=sonarr_cache,
                seerr_cache=seerr_cache,
            )

        await add_log("INFO", lm("scan.done", n=added), "job")
        await _purge_stale_seerr_items()
        await sync_emby_collection()
        await _send_pending_notifications()
        return "success", f"{added} queued"

    except Exception as e:
        logger.exception("Scan error")
        await add_log("ERROR", lm("scan.error", detail=e), "job")
        if await get_bool_setting("discord_alert_scan_failure"):
            _mention = await get_setting("discord_alert_scan_failure_mention") or ""
            _msg     = await get_setting("discord_alert_scan_failure_msg") or ""
            await send_alert(
                "🔴 Échec du scan", f"Le scan global a échoué : {e}", "error",
                mention=_mention, custom_msg=_msg,
                template_vars={"detail": str(e)},
            )
        return "error", str(e)


async def run_scan() -> None:
    """Full scan of all enabled libraries across all enabled servers.

    Hard timeout: 2 hours. If the scan exceeds this it is likely stuck on an
    unresponsive external service — the timeout forces a clean exit so the next
    scheduled run can start fresh.
    """
    if _scan_lock.locked():
        await add_log("WARN", lm("scan.already_running"), "job")
        return

    async with _scan_lock:
        run_id    = await add_job_run("scan")
        ctx_token = set_job_context(run_id)
        status, msg = "error", ""
        try:
            status, msg = await asyncio.wait_for(_run_scan_body(run_id), timeout=7200)
        except asyncio.TimeoutError:
            logger.error("run_scan exceeded 2-hour timeout — forcing exit")
            await add_log("ERROR", "Scan timeout (2h) — forcibly terminated", "job")
            msg = "timeout"
        finally:
            _current_job_id.reset(ctx_token)
            await finish_job_run(run_id, status, msg)


async def run_scan_library(library_id: str) -> None:
    """Scan a single library by ID."""
    if _scan_lock.locked():
        await add_log("WARN", lm("scan.already_running"), "job")
        return

    async with _scan_lock:
        run_id    = await add_job_run("scan_library")
        ctx_token = set_job_context(run_id)
        status, msg = "error", ""
        try:
            status, msg, _ = await _do_scan_one_library(library_id)
        except Exception as e:
            logger.exception("Scan library error")
            await add_log("ERROR", lm("scan.error", detail=e), "job")
            msg = str(e)
        finally:
            _current_job_id.reset(ctx_token)
            await finish_job_run(run_id, status, msg)
        await sync_emby_collection()
        await _send_pending_notifications()


async def run_scan_libraries(library_ids: list[str]) -> None:
    """Scan multiple libraries under a SINGLE lock acquisition.

    Unlike calling run_scan_library() N times (which releases and re-acquires
    the lock between each library), this holds the lock for the entire duration
    so the scheduled full scan cannot interrupt between library scans.

    Seerr/Radarr/Sonarr caches are built once and shared across all libraries,
    avoiding redundant external API calls for each library in the sequence.
    """
    if _scan_lock.locked():
        await add_log("WARN", lm("scan.already_running"), "job")
        return

    async with _scan_lock:
        try:
            seerr_cache, radarr_cache, sonarr_cache = await _build_shared_caches()
            for library_id in library_ids:
                run_id    = await add_job_run("scan_library")
                ctx_token = set_job_context(run_id)
                status, msg = "error", ""
                try:
                    status, msg, _ = await _do_scan_one_library(
                        library_id,
                        seerr_cache=seerr_cache,
                        radarr_cache=radarr_cache,
                        sonarr_cache=sonarr_cache,
                    )
                except Exception as e:
                    logger.exception("Scan library error (%s)", library_id)
                    await add_log("ERROR", lm("scan.error", detail=e), "job")
                    msg = str(e)
                finally:
                    _current_job_id.reset(ctx_token)
                    await finish_job_run(run_id, status, msg)
        except Exception as e:
            logger.exception("run_scan_libraries: cache build failed")
            await add_log("ERROR", lm("scan.error", detail=e), "scan")
        finally:
            # Always run post-scan operations, even if caches or a library fail
            await sync_emby_collection()
            await _send_pending_notifications()
