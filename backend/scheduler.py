"""
Scheduler — scan and deletion jobs.

Jobs:
  run_scan()                — full scan of all enabled libraries
  run_scan_library(id)      — scan one library
  run_deletion()            — process queue: notify (7d/1d) + delete (now)
  run_ignored_cleanup()     — remove expired entries from ignored_media
  sync_emby_collection()    — sync "Bientôt supprimé" collection in Emby

Internal:
  _evaluate_item            — apply rules to one Emby item
  _delete_media             — full deletion workflow (Discord → Emby → Arr → Seerr → qBit)
  _get_poster_url           — best-effort public poster URL
"""
import asyncio
import base64
import io
import json
import logging
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Optional, List

import aiosqlite
import httpx

from .database import (
    DB_PATH,
    STATUS_PENDING,
    STATUS_DELETED,
    STATUS_ERROR,
    TIMEOUT_SHORT,
    TIMEOUT_LONG,
    add_job_run,
    add_log,
    finish_job_run,
    get_setting,
    get_bool_setting,
    get_int_setting,
    get_media_servers,
    parse_iso_dt,
)
from .emby_client import (
    delete_item,
    get_client,
    get_client_ext_url,
    get_items_in_library,
    get_library_user_data,
    get_user_data,
    get_users,
)
from .arr_clients import (
    build_radarr_path_cache,
    build_seerr_request_cache,
    build_sonarr_path_cache,
    radarr_delete,
    radarr_find_by_path,
    radarr_find_by_path_cached,
    radarr_get_poster_url,
    radarr_get_torrent_hash,
    seerr_delete_request,
    seerr_find_request_by_tmdb,
    sonarr_delete_episode_file,
    sonarr_find_by_path,
    sonarr_find_by_path_cached,
    sonarr_get_poster_url,
    sonarr_get_torrent_hash,
)
from .qbit_client import qbit_add_tag, qbit_delete_torrent, qbit_find_by_path
from .discord_client import send_notification

logger = logging.getLogger(__name__)

# ─── Job locks (prevent concurrent runs of the same job) ─────────────────────
_scan_lock = asyncio.Lock()
_deletion_lock = asyncio.Lock()


def is_scan_running() -> bool:
    return _scan_lock.locked()


def is_deletion_running() -> bool:
    return _deletion_lock.locked()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ═══ Poster URL resolution ════════════════════════════════════════════════════
async def _get_poster_url(
    emby_id: str,
    tmdb_id: str = "",
    media_type: str = "Movie",
    radarr_id: Optional[int] = None,
    sonarr_id: Optional[int] = None,
    server_id: str = "0",
) -> str:
    """
    Return a publicly accessible poster URL.

    Priority:
      1. Radarr/Sonarr → TMDB remoteUrl (public CDN, survives deletion if cached)
      2. Emby external URL (if configured)
      3. Emby internal proxy (last resort, only works in-app, not Discord)
    """
    # 1. Radarr/Sonarr poster via TMDB
    try:
        if media_type == "Movie" and radarr_id:
            url = await radarr_get_poster_url(int(radarr_id))
            if url:
                return url
        elif media_type != "Movie" and sonarr_id:
            url = await sonarr_get_poster_url(int(sonarr_id))
            if url:
                return url
    except Exception as e:
        logger.debug(f"_get_poster_url arr lookup: {e}")

    # 2. Emby/Jellyfin external URL (if configured)
    try:
        emby_url, emby_key = await get_client(server_id)
        ext_url = await get_client_ext_url(server_id)
        if ext_url and emby_id:
            return f"{ext_url}/Items/{emby_id}/Images/Primary?api_key={emby_key}&maxHeight=300"
    except Exception:
        pass

    # 3. Internal proxy fallback (won't work in Discord but does in the app)
    try:
        emby_url, emby_key = await get_client(server_id)
        if emby_url and emby_key and emby_id:
            internal = (
                f"{emby_url}/Items/{emby_id}/Images/Primary"
                f"?api_key={emby_key}&maxHeight=300"
            )
            return f"/api/proxy/image?url={urllib.parse.quote(internal, safe='')}"
    except Exception:
        pass

    return ""


# ═══ Condition evaluation ═════════════════════════════════════════════════════
def _eval_op(actual, op: str, expected) -> bool:
    if actual is None:
        return False
    try:
        if op == "gt":
            return actual > expected
        if op == "gte":
            return actual >= expected
        if op == "lt":
            return actual < expected
        if op == "lte":
            return actual <= expected
        if op == "eq":
            return actual == expected
    except Exception:
        return False
    return False


def _days_since(dt: Optional[datetime]) -> Optional[int]:
    if not dt:
        return None
    return (now_utc() - dt).days


def _evaluate_conditions(
    conditions: list,
    logic: str,
    added_date: Optional[datetime],
    last_played: Optional[datetime],
    play_count: int,
    never_watched: bool,
) -> bool:
    """Check if a media item matches the library's conditions."""
    if not conditions:
        return False
    results = []
    for c in conditions:
        field = c.get("field")
        op = c.get("op", "gt")
        value = c.get("value", 0)
        if field == "days_since_added":
            results.append(_eval_op(_days_since(added_date), op, value))
        elif field == "days_not_watched":
            if never_watched:
                results.append(True)
            else:
                results.append(_eval_op(_days_since(last_played), op, value))
        elif field == "never_watched":
            results.append(never_watched == bool(value))
        elif field == "play_count":
            results.append(_eval_op(play_count, op, value))
        else:
            results.append(False)
    return any(results) if logic == "OR" else all(results)


def _seerr_filter_passes(seerr_user_id: Optional[int], seerr_conditions: list) -> bool:
    """Apply per-library Seerr user include/exclude filters."""
    if not seerr_conditions:
        return True
    includes = [c["user_id"] for c in seerr_conditions if c.get("type") == "user_include"]
    excludes = [c["user_id"] for c in seerr_conditions if c.get("type") == "user_exclude"]
    if includes and seerr_user_id not in includes:
        return False
    if excludes and seerr_user_id in excludes:
        return False
    return True


async def _get_seerr_grace(
    seerr_user_id: Optional[int], library_id: str, default_grace: int
) -> int:
    """Per-user grace days override (from seerr_user_rules)."""
    if not seerr_user_id:
        return default_grace
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT grace_days FROM seerr_user_rules "
            "WHERE seerr_user_id=? AND library_id=? AND enabled=1",
            (seerr_user_id, library_id),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else default_grace


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

                for lib in libraries:
                    added += await _scan_library(lib, user_ids, server_id=server_id)

            await add_log("INFO", f"Scan terminé — {added} média(s) ajouté(s)", "job")
            _scan_status, _scan_msg = "success", f"{added} queued"
            if added > 0:
                await sync_emby_collection()
                await _send_pending_notifications()
        except Exception as e:
            logger.exception("Scan error")
            await add_log("ERROR", f"Erreur scan: {e}", "job")
            _scan_msg = str(e)
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
            added = await _scan_library(lib, user_ids, server_id=server_id)

            await add_log("INFO", f"Scan terminé — {added} média(s) ajouté(s)", "job")
            _sl_status, _sl_msg = "success", f"{added} queued"

            if added > 0:
                await sync_emby_collection()
                await _send_pending_notifications()
        except Exception as e:
            logger.exception("Scan library error")
            await add_log("ERROR", f"Erreur scan: {e}", "job")
            _sl_msg = str(e)
        finally:
            await finish_job_run(run_id, _sl_status, _sl_msg)


async def _scan_library(lib: dict, user_ids: list, server_id: str = "0") -> int:
    """Scan one library — returns count of items added."""
    conditions = json.loads(lib.get("conditions") or "[]")
    logic = lib.get("logic") or "AND"
    grace_days = lib.get("grace_days") or 7
    seerr_conditions = json.loads(lib.get("seerr_conditions") or "[]")
    emby_library_id = lib["emby_library_id"]

    added = 0
    start = 0
    await add_log("INFO", f"Scan : {lib['name']}", "scan")

    # ── Build caches once per library (avoids per-item HTTP calls) ────────────
    user_data_cache: dict = {}
    for uid in user_ids:
        user_data_cache[uid] = await get_library_user_data(uid, emby_library_id, server_id=server_id)

    radarr_cache: dict = await build_radarr_path_cache()
    sonarr_cache: dict = await build_sonarr_path_cache()
    seerr_cache: dict = await build_seerr_request_cache()
    # Read once per library, not once per item
    seerr_ext_url: str = await get_setting("seerr_external_url") or ""

    while True:
        items, total = await get_items_in_library(
            emby_library_id, limit=500, start=start, server_id=server_id
        )
        if not items:
            break
        for item in items:
            if await _evaluate_item(
                item, lib, conditions, logic, grace_days, user_ids, seerr_conditions,
                user_data_cache=user_data_cache,
                radarr_cache=radarr_cache,
                sonarr_cache=sonarr_cache,
                seerr_cache=seerr_cache,
                seerr_ext=seerr_ext_url,
            ):
                added += 1
        start += 500
        if start >= total:
            break

    await add_log("INFO", f"{lib['name']} : {added} ajouté(s)", "scan")
    return added


async def _evaluate_item(
    item: dict,
    lib: dict,
    conditions: list,
    logic: str,
    grace_days: int,
    user_ids: list,
    seerr_conditions: list,
    *,
    user_data_cache: Optional[dict] = None,
    radarr_cache: Optional[dict] = None,
    sonarr_cache: Optional[dict] = None,
    seerr_cache: Optional[dict] = None,
    seerr_ext: str = "",
) -> bool:
    """Evaluate a single Emby item; insert into queue if it matches.

    user_data_cache: {uid: {emby_id: UserData}}       — pre-fetched per library.
    radarr_cache:    {file_path: radarr_id}            — pre-fetched once per scan.
    sonarr_cache:    {episode_file_path: ep_file_id}   — pre-fetched once per scan.
    seerr_cache:     {tmdb_id: {seerr_id, user_id, username}} — pre-fetched once per scan.
    Falls back to individual HTTP calls when caches are absent.
    """
    emby_id = item.get("Id")
    title = item.get("Name") or "?"
    media_type = item.get("Type") or ""
    file_path = item.get("Path") or ""
    tmdb_id = str(item.get("ProviderIds", {}).get("Tmdb") or "")
    date_str = item.get("DateCreated") or ""

    if not emby_id or not file_path:
        return False

    added_date = parse_iso_dt(date_str)
    if not added_date:
        return False

    # Aggregate UserData across users to determine play status.
    # Use the pre-fetched cache when available; fall back to per-item HTTP call.
    last_played: Optional[datetime] = None
    play_count = 0
    never_watched = True
    for uid in user_ids:
        if user_data_cache is not None:
            ud = user_data_cache.get(uid, {}).get(emby_id) or {}
        else:
            ud = await get_user_data(uid, emby_id) or {}
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
        return False

    # Skip if already in queue or ignored
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id FROM media_queue WHERE emby_id=?", (emby_id,)
        ) as cur:
            if await cur.fetchone():
                return False
        async with db.execute(
            "SELECT id FROM ignored_media WHERE emby_id=?", (emby_id,)
        ) as cur:
            if await cur.fetchone():
                return False

    # Seerr lookup — use pre-built cache when available (0 HTTP calls vs 1 per item)
    if seerr_cache is not None:
        seerr_data = seerr_cache.get(tmdb_id) if tmdb_id else None
    else:
        seerr_data = await seerr_find_request_by_tmdb(tmdb_id) if tmdb_id else None
    seerr_id = seerr_data.get("seerr_id") if seerr_data else None
    seerr_user_id = seerr_data.get("user_id") if seerr_data else None
    seerr_username = seerr_data.get("username", "") if seerr_data else ""

    # Apply Seerr filter
    if seerr_conditions:
        has_includes = any(c.get("type") == "user_include" for c in seerr_conditions)
        has_excludes = any(c.get("type") == "user_exclude" for c in seerr_conditions)
        if has_includes and not seerr_user_id:
            await add_log("INFO", f"Ignoré (non demandé sur Seerr) : {title}", "scan")
            return False
        if not _seerr_filter_passes(seerr_user_id, seerr_conditions):
            reason = "exclu" if has_excludes else "non inclus"
            await add_log("INFO", f"Ignoré (utilisateur Seerr {reason}) : {title}", "scan")
            return False

    # Compute grace + delete_at
    effective_grace = await _get_seerr_grace(seerr_user_id, lib["id"], grace_days)
    delete_at = now_utc() + timedelta(days=effective_grace)

    # Get Radarr/Sonarr ID FIRST so we can build the poster URL from TMDB.
    # Use pre-built caches (0 HTTP calls); fall back to individual calls if absent.
    radarr_id_val: Optional[int] = None
    sonarr_id_val: Optional[int] = None
    if media_type == "Movie":
        if radarr_cache is not None:
            radarr_id_val = radarr_find_by_path_cached(file_path, radarr_cache)
        else:
            radarr_id_val = await radarr_find_by_path(file_path)
    else:
        if sonarr_cache is not None:
            sonarr_id_val = sonarr_find_by_path_cached(file_path, sonarr_cache)
        else:
            sonarr_id_val = await sonarr_find_by_path(file_path)

    # Poster URL (will use Radarr/Sonarr → TMDB CDN if available)
    poster_url = await _get_poster_url(
        emby_id,
        tmdb_id=tmdb_id,
        media_type=media_type,
        radarr_id=radarr_id_val,
        sonarr_id=sonarr_id_val,
    )

    # Seerr request URL (for clickable link in UI)
    seerr_request_url = ""
    if seerr_id and seerr_ext:
        path = "movie" if media_type == "Movie" else "tv"
        seerr_request_url = f"{seerr_ext.rstrip('/')}/{path}/{tmdb_id}"

    # Insert into queue
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO media_queue
            (emby_id, title, media_type, library_id, library_name, file_path,
             poster_url, tmdb_id, seerr_id, seerr_user_id, seerr_username,
             seerr_request_url, radarr_id, sonarr_id,
             detected_at, delete_at, added_date, last_played, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                emby_id,
                title,
                media_type,
                lib["id"],
                lib["name"],
                file_path,
                poster_url,
                tmdb_id,
                seerr_id,
                seerr_user_id,
                seerr_username,
                seerr_request_url,
                radarr_id_val,
                sonarr_id_val,
                now_utc().isoformat(),
                delete_at.isoformat(),
                added_date.isoformat(),
                last_played.isoformat() if last_played else None,
            ),
        )
        await db.commit()

    note = ""
    if seerr_username and effective_grace != grace_days:
        note = f" (règle Seerr {seerr_username}: {effective_grace}j)"
    await add_log(
        "INFO",
        f"Ajouté : {title} → {delete_at.strftime('%d/%m/%Y')}{note}",
        "scan",
    )
    return True


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
                                    params={"api_key": emby_key_val},
                                    content=b64,
                                    headers={"Content-Type": "image/jpeg"},
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


_VALID_FLAG_COLS = frozenset({"notified_30d", "notified_7d", "notified_1d", "notified_now"})

async def _send_notif_batch(items: list, kind: str, flag_col: str, dry_run: bool):
    """Send a notification batch and mark items as notified only on success."""
    if flag_col not in _VALID_FLAG_COLS:
        raise ValueError(f"Invalid flag_col: {flag_col!r}")
    if not items:
        return
    sent = True
    if not dry_run:
        sent = await send_notification(items, kind, dry_run=False)
    if not sent:
        logger.warning(f"Discord notification '{kind}' failed — flag not set, will retry next cycle")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        ids = [r["id"] for r in items]
        placeholders = ",".join("?" * len(ids))
        await db.execute(
            f"UPDATE media_queue SET {flag_col}=1 WHERE id IN ({placeholders})", ids
        )
        await db.commit()


async def _send_pending_notifications():
    """
    Send 30d/7d/1d Discord notifications for newly queued items.
    Lightweight version of run_deletion() — no job history entry, no deletions.
    Called automatically after each scan so new items notify immediately.
    """
    dry_run = await get_bool_setting("dry_run")
    try:
        cutoff_30d = now_utc() + timedelta(days=30, hours=1)
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM media_queue WHERE status='pending' AND notified_30d=0 AND delete_at <= ?",
                (cutoff_30d.isoformat(),),
            ) as cur:
                to_30d = [dict(r) for r in await cur.fetchall()]
        await _send_notif_batch(to_30d, "30d", "notified_30d", dry_run)

        cutoff_7d = now_utc() + timedelta(days=7, hours=1)
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM media_queue WHERE status='pending' AND notified_7d=0 AND delete_at <= ?",
                (cutoff_7d.isoformat(),),
            ) as cur:
                to_7d = [dict(r) for r in await cur.fetchall()]
        await _send_notif_batch(to_7d, "7d", "notified_7d", dry_run)

        cutoff_1d = now_utc() + timedelta(hours=25)
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM media_queue WHERE status='pending' AND notified_1d=0 AND delete_at <= ?",
                (cutoff_1d.isoformat(),),
            ) as cur:
                to_1d = [dict(r) for r in await cur.fetchall()]
        await _send_notif_batch(to_1d, "1d", "notified_1d", dry_run)
    except Exception as e:
        logger.debug(f"_send_pending_notifications: {e}")


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

        try:
            # ── 30-day notifications ─────────────────────────────────────────
            cutoff_30d = now_utc() + timedelta(days=30, hours=1)
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM media_queue "
                    "WHERE status='pending' AND notified_30d=0 AND delete_at <= ?",
                    (cutoff_30d.isoformat(),),
                ) as cur:
                    to_30d = [dict(r) for r in await cur.fetchall()]
            await _send_notif_batch(to_30d, "30d", "notified_30d", dry_run)

            # ── 7-day notifications ──────────────────────────────────────────
            cutoff_7d = now_utc() + timedelta(days=7, hours=1)
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM media_queue "
                    "WHERE status='pending' AND notified_7d=0 AND delete_at <= ?",
                    (cutoff_7d.isoformat(),),
                ) as cur:
                    to_7d = [dict(r) for r in await cur.fetchall()]
            await _send_notif_batch(to_7d, "7d", "notified_7d", dry_run)

            # ── 1-day notifications ──────────────────────────────────────────
            cutoff_1d = now_utc() + timedelta(hours=25)
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM media_queue "
                    "WHERE status='pending' AND notified_1d=0 AND delete_at <= ?",
                    (cutoff_1d.isoformat(),),
                ) as cur:
                    to_1d = [dict(r) for r in await cur.fetchall()]
            await _send_notif_batch(to_1d, "1d", "notified_1d", dry_run)

            # ── Deletions ────────────────────────────────────────────────────
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM media_queue "
                    "WHERE status='pending' AND delete_at <= ?",
                    (now_utc().isoformat(),),
                ) as cur:
                    to_delete = [dict(r) for r in await cur.fetchall()]

            # Read qbit settings once for the whole batch
            _qbit_action = await get_setting("qbit_action") or "tag_only"
            _qbit_tag = await get_setting("qbit_tag") or "Supprimé-Hygie"
            for row in to_delete:
                ok = await _delete_media(row, dry_run, qbit_action=_qbit_action, qbit_tag_val=_qbit_tag)
                status_val = STATUS_DELETED if ok else STATUS_ERROR
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE media_queue SET status=?, notified_now=1 WHERE id=?",
                        (status_val, row["id"]),
                    )
                    await db.commit()
                if ok:
                    deleted_count += 1

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


async def _delete_media(row: dict, dry_run: bool, qbit_action: str = "", qbit_tag_val: str = "") -> bool:
    """
    Delete a media item across all services.

    Order matters:
      1. Find torrent hash (Radarr/Sonarr history) — BEFORE removing from arr
      2. Send Discord notification — BEFORE Emby deletion (image still accessible)
      3. Emby — remove hardlink
      4. Radarr/Sonarr — remove item (keep files)
      5. Seerr — delete request
      6. qBittorrent — tag OR delete torrent+files
    """
    title = row.get("title", "?")
    emby_id = row.get("emby_id")
    file_path = row.get("file_path", "")
    media_type = row.get("media_type", "")
    # Resolve server_id from the item's library for multi-server correctness
    library_server_id = "0"
    if row.get("library_id"):
        try:
            async with aiosqlite.connect(DB_PATH) as _ldb:
                async with _ldb.execute(
                    "SELECT server_id FROM libraries WHERE id=?", (row["library_id"],)
                ) as _cur:
                    _lrow = await _cur.fetchone()
                    if _lrow and _lrow[0]:
                        library_server_id = str(_lrow[0])
        except Exception:
            pass

    prefix = "[DRY RUN] " if dry_run else ""
    await add_log("INFO", f"{prefix}Suppression : {title}", "deletion")

    if dry_run:
        return True

    try:
        qbit_action = qbit_action or await get_setting("qbit_action") or "tag_only"
        qbit_tag = qbit_tag_val or await get_setting("qbit_tag") or "Supprimé-Hygie"

        # ── 1. Torrent hash via arr history (before deletion) ────────────────
        torrent_hash: Optional[str] = None
        if media_type == "Movie":
            rid = row.get("radarr_id") or await radarr_find_by_path(file_path)
            if rid:
                torrent_hash = await radarr_get_torrent_hash(int(rid))
        else:
            sid = row.get("sonarr_id") or await sonarr_find_by_path(file_path)
            if sid:
                torrent_hash = await sonarr_get_torrent_hash(int(sid))
        if not torrent_hash and file_path:
            torrent_hash = await qbit_find_by_path(file_path)

        # ── 2. Discord (BEFORE Emby — image still accessible) ────────────────
        try:
            await send_notification([row], "now", dry_run=False)
        except Exception as e:
            await add_log("WARN", f"Discord (non bloquant) : {e}", "deletion")

        # ── 3. Emby ──────────────────────────────────────────────────────────
        if emby_id:
            await delete_item(emby_id, server_id=library_server_id)
            await add_log("DEBUG", f"Emby : hardlink retiré pour {title}", "deletion")

        # ── 4. Radarr / Sonarr ───────────────────────────────────────────────
        if media_type == "Movie":
            rid = row.get("radarr_id") or await radarr_find_by_path(file_path)
            if rid:
                await radarr_delete(int(rid), delete_files=False)
                await add_log("DEBUG", f"Radarr : {title} retiré", "deletion")
        else:
            sid = row.get("sonarr_id") or await sonarr_find_by_path(file_path)
            if sid:
                await sonarr_delete_episode_file(int(sid))
                await add_log("DEBUG", f"Sonarr : {title} retiré", "deletion")

        # ── 5. Seerr ─────────────────────────────────────────────────────────
        if row.get("seerr_id"):
            await seerr_delete_request(row["seerr_id"])
            await add_log("DEBUG", f"Seerr : requête {title} supprimée", "deletion")

        # ── 6. qBittorrent ───────────────────────────────────────────────────
        if torrent_hash:
            try:
                if qbit_action == "delete_torrent":
                    ok = await qbit_delete_torrent(torrent_hash, delete_files=True)
                    msg = (
                        f"qBittorrent : torrent + fichier supprimés pour {title}"
                        if ok
                        else f"qBittorrent : échec suppression {title}"
                    )
                    await add_log("INFO" if ok else "WARN", msg, "deletion")
                else:
                    ok = await qbit_add_tag(torrent_hash, qbit_tag)
                    msg = (
                        f"qBittorrent : tag '{qbit_tag}' ajouté à {title}"
                        if ok
                        else f"qBittorrent : échec tag {title}"
                    )
                    await add_log("INFO" if ok else "WARN", msg, "deletion")
            except Exception as e:
                await add_log(
                    "WARN", f"qBittorrent erreur pour {title}: {e}", "deletion"
                )
        else:
            await add_log(
                "INFO",
                f"qBittorrent : torrent non trouvé pour {title} (ignoré)",
                "deletion",
            )

        await add_log("INFO", f"Suppression complète : {title}", "deletion")
        # Increment global stats
        try:
            month = now_utc().strftime("%Y-%m")
            async with aiosqlite.connect(DB_PATH) as _db:
                await _db.execute(
                    "INSERT INTO stats_history (ts, total_deleted, total_scanned, space_freed_bytes, month) "
                    "VALUES (?, 1, 0, 0, ?)",
                    (now_utc().isoformat(), month)
                )
                await _db.commit()
        except Exception:
            pass
        return True
    except Exception as e:
        logger.exception(f"_delete_media {title}")
        await add_log("ERROR", f"Erreur suppression {title}: {e}", "deletion")
        return False


# ═══ Ignored cleanup ══════════════════════════════════════════════════════════
async def run_ignored_cleanup():
    """Remove expired ignored_media entries + purge old deleted queue entries + rotate logs."""
    now = now_utc().isoformat()
    purged_rows = 0

    # Expire ignored items
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT title FROM ignored_media "
            "WHERE expire_at IS NOT NULL AND expire_at <= ?",
            (now,),
        ) as cur:
            expired = await cur.fetchall()
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
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    "SELECT COUNT(*) FROM media_queue "
                    "WHERE status='deleted' AND detected_at < ?",
                    (cutoff,),
                ) as cur:
                    count = (await cur.fetchone())[0]
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
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    "SELECT COUNT(*) FROM logs WHERE ts < ?", (cutoff,)
                ) as cur:
                    count = (await cur.fetchone())[0]
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
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    "SELECT COUNT(*) FROM job_history WHERE started_at < ?", (cutoff,)
                ) as cur:
                    count = (await cur.fetchone())[0]
                if count:
                    await db.execute(
                        "DELETE FROM job_history WHERE started_at < ?", (cutoff,)
                    )
                    await db.commit()
                    purged_rows += count
    except Exception as e:
        logger.debug(f"Purge job_history: {e}")

    # WAL checkpoint to reclaim disk space — non-blocking unlike VACUUM
    if purged_rows > 1000:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            await add_log("INFO", "WAL checkpoint exécuté — espace disque libéré", "system")
        except Exception as e:
            logger.debug(f"WAL checkpoint: {e}")


# ═══ Emby "Bientôt supprimé" collection sync ═════════════════════════════════
async def _overlay_poster(image_bytes: bytes, days_left: int) -> Optional[bytes]:
    """Add a colored banner 'Supprimé dans Xj' at the bottom of a poster."""
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        w, h = img.size

        # Banner at the BOTTOM — ~13% of height, font fills the banner
        banner_h = max(52, int(h * 0.13))

        banner = Image.new("RGBA", (w, banner_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(banner)
        draw.rectangle([0, 0, w, banner_h], fill=(200, 30, 30, 230))

        # Bilingual overlay label based on Hygie language setting (stored in DB)
        try:
            ui_lang = await get_setting("ui_language") or "fr"
        except Exception:
            ui_lang = "fr"
        if ui_lang == "en":
            label = f"Deleted in {days_left}d" if days_left > 0 else "Imminent"
        else:
            label = f"Supprimé dans {days_left}j" if days_left > 0 else "Imminent"

        # Find largest font that fits banner width and height
        font_paths = (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        )
        font = None
        max_font = banner_h - 10  # fill the banner, leave 5px padding top/bottom
        for size in range(max_font, 8, -1):
            for fp in font_paths:
                try:
                    candidate = ImageFont.truetype(fp, size)
                    bbox = draw.textbbox((0, 0), label, font=candidate)
                    text_w = bbox[2] - bbox[0]
                    text_h = bbox[3] - bbox[1]
                    if text_w <= w - 8 and text_h <= banner_h - 4:
                        font = candidate
                        break
                except Exception:
                    continue
            if font:
                break
        if font is None:
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), label, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = max(0, (w - text_w) // 2)
        y = max(0, (banner_h - text_h) // 2)
        draw.text((x + 1, y + 1), label, font=font, fill=(0, 0, 0, 160))
        draw.text((x, y), label, font=font, fill=(255, 255, 255, 255))

        result = img.copy()
        result.paste(banner, (0, h - banner_h), banner)

        out = io.BytesIO()
        result.convert("RGB").save(out, format="JPEG", quality=88)
        logger.debug(f"Overlay généré: {w}x{h}, banner={banner_h}px, label={label!r}")
        return out.getvalue()
    except Exception as e:
        logger.warning(f"_overlay_poster error: {e}")
        return None


async def sync_emby_collection():
    """Sync the 'Bientôt supprimé' collection — compatible with both Emby and Jellyfin.
    Uses the first enabled server from media_servers; falls back to legacy server "0".
    """
    # Find first enabled server that supports collections (emby or jellyfin)
    sync_server_id = "0"
    servers = await get_media_servers()
    enabled = [s for s in servers if s.get("enabled", True) and s.get("type") in ("emby", "jellyfin", "")]
    if enabled:
        sync_server_id = str(enabled[0].get("id", "0"))
    elif servers:
        # No enabled server with known type — skip
        first_type = servers[0].get("type", "")
        if first_type not in ("emby", "jellyfin", ""):
            return
    # Skip for truly unknown/untested servers (legacy path)
    if not servers:
        server_type = await get_setting("media_server_type")
        if server_type not in ("emby", "jellyfin", ""):
            return
    collection_name = await get_setting("emby_leaving_soon_collection")
    if not collection_name:
        return

    try:
        days = await get_int_setting("emby_leaving_soon_days", 30)
    except ValueError:
        days = 30

    cutoff = now_utc() + timedelta(days=days)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT emby_id, title, delete_at, poster_url FROM media_queue "
            "WHERE status='pending' AND delete_at <= ?",
            (cutoff.isoformat(),),
        ) as cur:
            wanted = [dict(r) for r in await cur.fetchall()]

    wanted_ids = {w["emby_id"] for w in wanted}

    emby_url, emby_key = await get_client(sync_server_id)
    if not emby_url or not emby_key:
        return

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Find or create collection
            r = await client.get(
                f"{emby_url}/Items",
                params={
                    "api_key": emby_key,
                    "IncludeItemTypes": "BoxSet",
                    "Recursive": "true",
                    "SearchTerm": collection_name,
                    "Limit": 10,
                },
            )
            collection_id = None
            if r.status_code == 200:
                for item in r.json().get("Items", []):
                    if item.get("Name", "").lower() == collection_name.lower():
                        collection_id = item["Id"]
                        break

            ru = await client.get(
                f"{emby_url}/Users", params={"api_key": emby_key}
            )
            user_id = ""
            if ru.status_code == 200 and ru.json():
                user_id = ru.json()[0]["Id"]

            if not collection_id:
                if not wanted_ids:
                    return
                rc = await client.post(
                    f"{emby_url}/Collections",
                    params={
                        "api_key": emby_key,
                        "Name": collection_name,
                        "Ids": ",".join(wanted_ids),
                        "UserId": user_id,
                    },
                )
                if rc.status_code in (200, 204):
                    collection_id = rc.json().get("Id")
                    await add_log(
                        "INFO",
                        f"Collection Emby '{collection_name}' créée ({len(wanted_ids)} médias)",
                        "system",
                    )
                return

            # Current items in collection
            rc = await client.get(
                f"{emby_url}/Items",
                params={
                    "api_key": emby_key,
                    "ParentId": collection_id,
                    "Recursive": "false",
                    "Limit": 5000,
                    "Fields": "Id",
                },
            )
            current_ids: set = set()
            if rc.status_code == 200:
                for item in rc.json().get("Items", []):
                    current_ids.add(item["Id"])

            to_add = wanted_ids - current_ids
            to_remove = current_ids - wanted_ids

            if to_add:
                await client.post(
                    f"{emby_url}/Collections/{collection_id}/Items",
                    params={"api_key": emby_key, "Ids": ",".join(to_add)},
                )
            if to_remove:
                await client.delete(
                    f"{emby_url}/Collections/{collection_id}/Items",
                    params={"api_key": emby_key, "Ids": ",".join(to_remove)},
                )
                # Restore original poster for removed items (remove the overlay)
                overlay_enabled_check = await get_bool_setting("emby_leaving_soon_overlay")
                if overlay_enabled_check:
                    # Find poster_url for removed items from media_queue (any status)
                    async with aiosqlite.connect(DB_PATH) as _db:
                        _db.row_factory = aiosqlite.Row
                        for emby_id in to_remove:
                            try:
                                async with _db.execute(
                                    "SELECT poster_url FROM media_queue WHERE emby_id=?",
                                    (emby_id,),
                                ) as cur:
                                    row = await cur.fetchone()
                                poster_url = dict(row)["poster_url"] if row else ""
                                if poster_url and poster_url.startswith("http"):
                                    pr = await client.get(poster_url, follow_redirects=True)
                                    if pr.status_code == 200 and pr.headers.get("content-type","").startswith("image"):
                                        b64 = base64.b64encode(pr.content).decode("ascii")
                                        resp = await client.post(
                                            f"{emby_url}/Items/{emby_id}/Images/Primary",
                                            params={"api_key": emby_key},
                                            content=b64,
                                            headers={"Content-Type": "image/jpeg"},
                                        )
                                        if resp.status_code in (200, 204):
                                            await add_log("INFO", f"Affiche restaurée : {emby_id}", "system")
                                        else:
                                            logger.warning(f"Restore poster HTTP {resp.status_code} pour {emby_id}")
                            except Exception as e_restore:
                                logger.warning(f"Restore poster error pour {emby_id}: {e_restore}")

            # Apply overlay to ALL items — ensures banners are always correct
            # after poster refresh, Emby restart, or code updates.
            overlay_enabled = await get_bool_setting("emby_leaving_soon_overlay")
            if overlay_enabled and wanted:
                for w in wanted:
                    try:
                        dt = parse_iso_dt(w["delete_at"])
                        if not dt:
                            continue
                        days_left = max(0, (dt - now_utc()).days)

                        # Fetch ORIGINAL poster from TMDB (via poster_url stored at scan time)
                        # Never use Emby as source — it may already have a corrupted overlay
                        original_bytes = None
                        poster_url = w.get("poster_url", "")
                        if poster_url and poster_url.startswith("http"):
                            try:
                                pr = await client.get(poster_url, follow_redirects=True)
                                if pr.status_code == 200 and pr.headers.get("content-type","").startswith("image"):
                                    original_bytes = pr.content
                            except Exception as e_poster:
                                logger.debug(f"Poster fetch from URL failed: {e_poster}")
                        
                        # Fallback: try Emby only if we have no other source
                        if not original_bytes:
                            pr = await client.get(
                                f"{emby_url}/Items/{w['emby_id']}/Images/Primary",
                                params={"api_key": emby_key, "maxHeight": 600},
                            )
                            if pr.status_code == 200:
                                original_bytes = pr.content

                        if not original_bytes:
                            continue
                        modified = await _overlay_poster(original_bytes, days_left)
                        if not modified:
                            continue
                        # Emby image upload: body must be base64-encoded
                        b64 = base64.b64encode(modified).decode("ascii")
                        resp = await client.post(
                            f"{emby_url}/Items/{w['emby_id']}/Images/Primary",
                            params={"api_key": emby_key},
                            content=b64,
                            headers={"Content-Type": "image/jpeg"},
                        )
                        if resp.status_code in (200, 204):
                            await add_log("INFO", f"Overlay appliqué : {w.get('title')}", "system")
                        else:
                            logger.warning(
                                f"Overlay upload HTTP {resp.status_code} "
                                f"pour {w.get('title')}: {resp.text[:100]}"
                            )
                    except Exception as e:
                        logger.warning(f"Overlay error pour {w.get('title','?')}: {e}")

            total = len(wanted_ids)
            if to_add or to_remove:
                await add_log(
                    "INFO",
                    f"Collection '{collection_name}' : {total} médias | "
                    f"+{len(to_add)} ajoutés | -{len(to_remove)} retirés",
                    "system",
                )
    except Exception as e:
        logger.exception("sync_emby_collection")
        await add_log("ERROR", f"Erreur sync collection Emby: {e}", "system")
