"""
Scanner — scan jobs extracted from scheduler.py.

Functions:
  run_scan()                     — full scan of all enabled libraries
  run_scan_library(id)           — scan one library
  _scan_library(...)             — scan one library (internal)
  _insert_queue_entry(...)       — insert one item into media_queue
  _consolidate_and_insert(...)   — group episodes by season/series before inserting
  reevaluate_library_queue(id)   — recheck conditions for pending items
"""
import asyncio
import base64
import json
import logging
from collections import defaultdict
from datetime import timedelta
from typing import Optional

import aiosqlite
import httpx

from .db.utils import DB_PATH, STATUS_PENDING, now_utc, parse_iso_dt
from .db.settings_store import get_setting, get_bool_setting, get_int_setting
from .db.media_servers import get_media_servers
from .db.logs import add_job_run, add_log, finish_job_run
from .emby_client import (
    delete_item,
    get_client,
    get_items_in_library,
    get_library_user_data,
    get_user_data,
    get_users,
)
from .arr_clients import (
    build_radarr_path_cache,
    build_seerr_request_cache,
    build_sonarr_path_cache,
)
from .exceptions import ArrClientError
from .discord_client import send_alert, send_notification
from .conditions import _evaluate_conditions, _evaluate_item
from .notifications import _ensure_notif_columns, _send_pending_notifications
from .collection import sync_emby_collection
from ._job_state import _scan_lock

logger = logging.getLogger(__name__)


# ═══ Scan ════════════════════════════════════════════════════════════════════
async def run_scan():
    """Full scan of all enabled libraries."""
    if _scan_lock.locked():
        await add_log("WARN", "Scan déjà en cours — ignoré", "job")
        return

    async with _scan_lock:
        run_id = await add_job_run("scan")
        await add_log("INFO", "Scan démarré", "job")
        added = 0
        _scan_status, _scan_msg = "error", ""
        await _ensure_notif_columns()
        try:
            # Get enabled servers — fall back to a single "legacy" server if none configured
            servers = await get_media_servers()
            enabled_servers = [s for s in servers if s.get("enabled", True)]
            if not enabled_servers:
                # Legacy: treat as single server with id "0"
                enabled_servers = [{"id": "0", "type": await get_setting("media_server_type") or "emby"}]

            for server in enabled_servers:
                server_id = str(server.get("id", "0"))
                server_type = server.get("type", "")
                # Skip Jellyfin/unknown for scan operations (same API, but log for clarity)
                if server_type not in ("emby", "jellyfin", ""):
                    await add_log("INFO", f"Serveur {server_id} ignoré (type: {server_type})", "scan")
                    continue

                async with aiosqlite.connect(DB_PATH) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute(
                        "SELECT * FROM libraries WHERE enabled=1 AND (server_id=? OR server_id IS NULL OR server_id='')",
                        (server_id,)
                    ) as cur:
                        libraries = [dict(r) for r in await cur.fetchall()]

                if not libraries:
                    continue

                users = await get_users(server_id=server_id)
                user_ids = [u["Id"] for u in users] if users else []

                # Build arr caches once per server (not once per library)
                radarr_cache = await build_radarr_path_cache()
                sonarr_cache = await build_sonarr_path_cache()
                seerr_cache: dict = {}
                try:
                    seerr_cache = await build_seerr_request_cache()
                except ArrClientError as _seerr_err:
                    await add_log("WARN", f"Seerr inaccessible : {_seerr_err}", "scan")
                    if await get_bool_setting("discord_alert_seerr_failure"):
                        _mention = await get_setting("discord_alert_seerr_failure_mention") or ""
                        _msg = await get_setting("discord_alert_seerr_failure_msg") or ""
                        await send_alert(
                            "🔌 Seerr inaccessible", str(_seerr_err), "warning",
                            mention=_mention, custom_msg=_msg,
                            template_vars={"detail": str(_seerr_err)},
                        )

                # Pre-load queued/ignored sets — eliminates 2 DB opens per item
                async with aiosqlite.connect(DB_PATH) as _db:
                    async with _db.execute("SELECT emby_id FROM media_queue") as _cur:
                        queued_ids = {r[0] async for r in _cur}
                    async with _db.execute("SELECT emby_id FROM ignored_media") as _cur:
                        ignored_ids = {r[0] async for r in _cur}

                try:
                    max_parallel = int(await get_setting("max_parallel_library_scans") or "3")
                except (ValueError, TypeError):
                    max_parallel = 3
                _lib_sem = asyncio.Semaphore(max(1, max_parallel))

                async def _scan_lib_with_sem(lib):
                    async with _lib_sem:
                        return await _scan_library(
                            lib, user_ids, server_id=server_id,
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
                        await add_log("ERROR", f"Erreur scan bibliothèque: {r}", "scan")

            await add_log("INFO", f"Scan terminé — {added} média(s) ajouté(s)", "job")
            _scan_status, _scan_msg = "success", f"{added} queued"
            await sync_emby_collection()
            await _send_pending_notifications()
        except Exception as e:
            logger.exception("Scan error")
            await add_log("ERROR", f"Erreur scan: {e}", "job")
            _scan_msg = str(e)
            if await get_bool_setting("discord_alert_scan_failure"):
                _mention = await get_setting("discord_alert_scan_failure_mention") or ""
                _msg = await get_setting("discord_alert_scan_failure_msg") or ""
                await send_alert(
                    "🔴 Échec du scan", f"Le scan global a échoué : {e}", "error",
                    mention=_mention, custom_msg=_msg,
                    template_vars={"detail": str(e)},
                )
        finally:
            await finish_job_run(run_id, _scan_status, _scan_msg)


async def run_scan_library(library_id: str):
    """Scan a single library."""
    if _scan_lock.locked():
        await add_log("WARN", "Scan déjà en cours — ignoré", "job")
        return

    async with _scan_lock:
        run_id = await add_job_run("scan_library")
        await add_log("INFO", f"Scan bibliothèque : {library_id}", "job")
        _sl_status, _sl_msg = "error", ""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM libraries WHERE id=? AND enabled=1",
                    (library_id,),
                ) as cur:
                    row = await cur.fetchone()
                lib = dict(row) if row else None

            if not lib:
                await add_log("WARN", f"Bibliothèque {library_id} introuvable", "scan")
                _sl_status, _sl_msg = "warning", "Library not found"
                return

            server_id = str(lib.get("server_id") or "0")
            users = await get_users(server_id=server_id)
            user_ids = [u["Id"] for u in users] if users else []

            radarr_cache = await build_radarr_path_cache()
            sonarr_cache = await build_sonarr_path_cache()
            seerr_cache: dict = {}
            try:
                seerr_cache = await build_seerr_request_cache()
            except ArrClientError as _seerr_err:
                await add_log("WARN", f"Seerr inaccessible : {_seerr_err}", "scan")
                if await get_bool_setting("discord_alert_seerr_failure"):
                    _mention = await get_setting("discord_alert_seerr_failure_mention") or ""
                    _msg = await get_setting("discord_alert_seerr_failure_msg") or ""
                    await send_alert(
                        "🔌 Seerr inaccessible", str(_seerr_err), "warning",
                        mention=_mention, custom_msg=_msg,
                        template_vars={"detail": str(_seerr_err)},
                    )
            async with aiosqlite.connect(DB_PATH) as _db:
                async with _db.execute("SELECT emby_id FROM media_queue") as _cur:
                    queued_ids = {r[0] async for r in _cur}
                async with _db.execute("SELECT emby_id FROM ignored_media") as _cur:
                    ignored_ids = {r[0] async for r in _cur}

            added = await _scan_library(
                lib, user_ids, server_id=server_id,
                radarr_cache=radarr_cache, sonarr_cache=sonarr_cache,
                seerr_cache=seerr_cache,
                queued_ids=queued_ids, ignored_ids=ignored_ids,
            )

            await add_log("INFO", f"Scan terminé — {added} média(s) ajouté(s)", "job")
            _sl_status, _sl_msg = "success", f"{added} queued"
            await sync_emby_collection()
            await _send_pending_notifications()
        except Exception as e:
            logger.exception("Scan library error")
            await add_log("ERROR", f"Erreur scan: {e}", "job")
            _sl_msg = str(e)
        finally:
            await finish_job_run(run_id, _sl_status, _sl_msg)


async def _scan_library(
    lib: dict,
    user_ids: list,
    server_id: str = "0",
    *,
    radarr_cache: Optional[dict] = None,
    sonarr_cache: Optional[dict] = None,
    seerr_cache: Optional[dict] = None,
    queued_ids: Optional[set] = None,
    ignored_ids: Optional[set] = None,
) -> int:
    """Scan one library — returns count of items added.

    Caches (radarr/sonarr/seerr) and sets (queued/ignored) should be built
    once in the calling scan job and passed here to avoid redundant HTTP/DB calls.
    """
    conditions = json.loads(lib.get("conditions") or "[]")
    logic = lib.get("logic") or "AND"
    grace_days = lib.get("grace_days") or 7
    seerr_conditions = json.loads(lib.get("seerr_conditions") or "[]")
    emby_library_id = lib["emby_library_id"]
    deletion_unit = lib.get("deletion_unit") or "episode"

    added = 0
    start = 0
    await add_log("INFO", f"Scan : {lib['name']}", "scan")

    # User data cache: one HTTP call per user per library
    user_data_cache: dict = {}
    for uid in user_ids:
        user_data_cache[uid] = await get_library_user_data(uid, emby_library_id, server_id=server_id)

    seerr_ext_url: str = await get_setting("seerr_external_url") or ""
    dry_run = await get_bool_setting("dry_run")

    # Collect ALL eligible items before deciding how to insert
    eligible: list = []
    while True:
        items, total = await get_items_in_library(
            emby_library_id, limit=500, start=start, server_id=server_id
        )
        if not items:
            break
        for item in items:
            entry = await _evaluate_item(
                item, lib, conditions, logic, grace_days, user_ids, seerr_conditions,
                user_data_cache=user_data_cache,
                radarr_cache=radarr_cache,
                sonarr_cache=sonarr_cache,
                seerr_cache=seerr_cache,
                seerr_ext=seerr_ext_url,
                queued_ids=queued_ids,
                ignored_ids=ignored_ids,
            )
            if entry is not None:
                eligible.append(entry)
        start += 500
        if start >= total:
            break

    if deletion_unit == "episode":
        for entry in eligible:
            await _insert_queue_entry(entry, queued_ids, dry_run)
            added += 1
    else:
        added = await _consolidate_and_insert(
            lib, eligible, sonarr_cache or {}, deletion_unit, queued_ids, dry_run
        )

    await add_log("INFO", f"{lib['name']} : {added} ajouté(s)", "scan")
    return added


async def _insert_queue_entry(entry: dict, queued_ids: Optional[set], dry_run: bool) -> None:
    """Insert one eligible item into media_queue and send detected notification."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO media_queue
            (emby_id, title, media_type, library_id, library_name, file_path,
             poster_url, tmdb_id, seerr_id, seerr_user_id, seerr_username,
             seerr_request_url, radarr_id, sonarr_id, sonarr_series_id, season_number,
             detected_at, delete_at, added_date, last_played, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (
                entry["emby_id"], entry["title"], entry["media_type"],
                entry["library_id"], entry["library_name"], entry["file_path"],
                entry["poster_url"], entry["tmdb_id"],
                entry["seerr_id"], entry["seerr_user_id"], entry["seerr_username"],
                entry["seerr_request_url"], entry["radarr_id"], entry["sonarr_id"],
                entry.get("sonarr_series_id"), entry.get("season_number"),
                entry["detected_at"], entry["delete_at"],
                entry["added_date"], entry["last_played"],
            ),
        )
        await db.commit()
    if queued_ids is not None:
        queued_ids.add(entry["emby_id"])
    item_notif = {k: entry[k] for k in ("title", "media_type", "library_name",
                                         "seerr_user_id", "seerr_username",
                                         "poster_url", "delete_at")}
    try:
        sent = await send_notification([item_notif], "detected", dry_run=dry_run)
        if sent:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE media_queue SET notified_detected=1 WHERE emby_id=? AND status='pending'",
                    (entry["emby_id"],),
                )
                await db.commit()
    except Exception as e:
        logger.warning(f"Detected notification failed for {entry['title']}: {e}")


async def _consolidate_and_insert(
    lib: dict,
    eligible: list,
    sonarr_cache: dict,
    deletion_unit: str,
    queued_ids: Optional[set],
    dry_run: bool,
) -> int:
    """Group eligible episodes by season or series and insert one queue entry per group.

    Only groups where ALL episode files in that season/series are eligible get inserted.
    Returns count of consolidated entries added.
    """
    from collections import defaultdict

    # Count total episode files per (series_id, season) and per series_id from cache
    season_totals: dict = defaultdict(int)
    series_totals: dict = defaultdict(int)
    for cache_entry in sonarr_cache.values():
        if not isinstance(cache_entry, dict):
            continue
        sid = cache_entry.get("series_id")
        sn = cache_entry.get("season_number")
        if sid is not None and sn is not None:
            season_totals[(sid, sn)] += 1
            series_totals[sid] += 1

    if deletion_unit == "season":
        groups: dict = defaultdict(list)
        for ep in eligible:
            sid = ep.get("sonarr_series_id")
            sn = ep.get("season_number")
            if sid is not None and sn is not None:
                groups[(sid, sn)].append(ep)

        added = 0
        for (sid, sn), eps in groups.items():
            total = season_totals.get((sid, sn), 0)
            if total > 0 and len(eps) >= total:
                # Whole season eligible — use the episode with latest delete_at
                anchor = max(eps, key=lambda e: e["delete_at"])
                series_title = (sonarr_cache.get(anchor["file_path"]) or {}).get(
                    "series_title", anchor["title"]
                )
                poster = (sonarr_cache.get(anchor["file_path"]) or {}).get("poster_url", "")
                consolidated = {
                    **anchor,
                    "emby_id": f"sonarr-season:{sid}:{sn}",
                    "title": f"{series_title} — Saison {sn}",
                    "sonarr_id": None,
                    "sonarr_series_id": sid,
                    "season_number": sn,
                    "poster_url": poster or anchor["poster_url"],
                    "file_path": anchor["file_path"],
                }
                if queued_ids is None or consolidated["emby_id"] not in queued_ids:
                    await _insert_queue_entry(consolidated, queued_ids, dry_run)
                    added += 1
        return added

    elif deletion_unit == "series":
        groups_s: dict = defaultdict(list)
        for ep in eligible:
            sid = ep.get("sonarr_series_id")
            if sid is not None:
                groups_s[sid].append(ep)

        added = 0
        for sid, eps in groups_s.items():
            total = series_totals.get(sid, 0)
            if total > 0 and len(eps) >= total:
                anchor = max(eps, key=lambda e: e["delete_at"])
                cache_entry = next(
                    (v for v in sonarr_cache.values()
                     if isinstance(v, dict) and v.get("series_id") == sid),
                    {}
                )
                series_title = cache_entry.get("series_title", anchor["title"])
                poster = cache_entry.get("poster_url", "")
                consolidated = {
                    **anchor,
                    "emby_id": f"sonarr-series:{sid}",
                    "title": series_title,
                    "sonarr_id": None,
                    "sonarr_series_id": sid,
                    "season_number": None,
                    "poster_url": poster or anchor["poster_url"],
                    "file_path": anchor["file_path"],
                }
                if queued_ids is None or consolidated["emby_id"] not in queued_ids:
                    await _insert_queue_entry(consolidated, queued_ids, dry_run)
                    added += 1
        return added

    return 0


# ═══ Reevaluate library queue (when conditions change) ════════════════════════
async def reevaluate_library_queue(library_id: str) -> int:
    """Recheck conditions for pending items in a library. Remove those no longer matching."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM libraries WHERE id=?", (library_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return 0
            lib = dict(row)
        async with db.execute(
            "SELECT * FROM media_queue WHERE library_id=? AND status='pending'",
            (library_id,),
        ) as cur:
            pending = [dict(r) for r in await cur.fetchall()]

    if not pending:
        return 0

    conditions = json.loads(lib.get("conditions") or "[]")
    logic = lib.get("logic") or "AND"

    server_id = str(lib.get("server_id") or "0")
    users = await get_users(server_id=server_id)
    user_ids = [u["Id"] for u in users] if users else []
    removed = 0

    for row in pending:
        emby_id = row["emby_id"]
        added_date = None
        last_played = None
        play_count = 0
        never_watched = True
        added_date = parse_iso_dt(row.get("added_date"))
        raw_lp = parse_iso_dt(row.get("last_played"))
        if raw_lp:
            last_played = raw_lp
            never_watched = False

        for uid in user_ids:
            ud = await get_user_data(uid, emby_id)
            if not ud:
                continue
            pc = ud.get("PlayCount") or 0
            play_count = max(play_count, pc)
            if ud.get("Played") or pc > 0:
                never_watched = False
                lp_str = ud.get("LastPlayedDate") or ""
                if lp_str:
                    lp = parse_iso_dt(lp_str)
                    if lp and (last_played is None or lp > last_played):
                        last_played = lp

        if not _evaluate_conditions(
            conditions, logic, added_date, last_played, play_count, never_watched
        ):
            # Restore original poster before removing from queue
            poster_url = row.get("poster_url", "")
            emby_id = row.get("emby_id", "")
            if poster_url and poster_url.startswith("http") and emby_id:
                try:
                    _lib_srv = str(lib.get("server_id") or "0")
                    emby_url_val, emby_key_val = await get_client(_lib_srv)
                    overlay_on = await get_bool_setting("emby_leaving_soon_overlay")
                    if overlay_on:
                        async with httpx.AsyncClient(timeout=10) as hc:
                            pr = await hc.get(poster_url, follow_redirects=True)
                            if pr.status_code == 200 and pr.headers.get("content-type","").startswith("image"):
                                b64 = base64.b64encode(pr.content).decode("ascii")
                                await hc.post(
                                    f"{emby_url_val}/Items/{emby_id}/Images/Primary",
                                    headers={"X-Emby-Token": emby_key_val, "Content-Type": "image/jpeg"},
                                    content=b64,
                                )
                except Exception as e_rp:
                    logger.debug(f"Restore poster (reevaluate): {e_rp}")

            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "DELETE FROM media_queue WHERE id=?", (row["id"],)
                )
                await db.commit()
            removed += 1
            await add_log(
                "INFO", f"Retiré (conditions changées) : {row['title']}", "scan"
            )

    if removed:
        await add_log(
            "INFO",
            f"Réévaluation {lib['name']} : {removed} média(s) retirés",
            "scan",
        )
        await sync_emby_collection()
    return removed
