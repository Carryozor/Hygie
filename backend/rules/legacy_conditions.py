"""
Condition evaluation — pure functions + _evaluate_item orchestrator.

Public API:
  _eval_op                  — compare actual vs expected with an operator
  _evaluate_conditions      — apply all library conditions to a media item
  _seerr_filter_passes      — Seerr include/exclude user filter
  _get_seerr_grace          — per-user grace days override
  _update_delete_at_if_pending — recalculate delete_at when grace changes
  _get_poster_url           — resolve best-effort public poster URL
  _evaluate_item            — full evaluation + DB insert for one Emby item
"""
import logging
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from ..db.utils import now_utc, parse_iso_dt
from ..db.engine import get_db
from ..db.logs import add_log
from ..arr_clients import (
    radarr_find_by_path,
    radarr_find_by_path_cached,
    radarr_get_poster_url,
    seerr_find_request_by_tmdb,
    sonarr_find_by_path,
    sonarr_get_cache_entry,
    sonarr_get_poster_url,
)
from ..emby_client import get_client, get_user_data

logger = logging.getLogger(__name__)


# ─── Scan context ─────────────────────────────────────────────────────────────

@dataclass
class ScanContext:
    """Scan-level caches passed into _evaluate_item to avoid per-item fetches."""
    user_data_cache: dict = field(default_factory=dict)
    radarr_cache: Optional[dict] = None
    sonarr_cache: Optional[dict] = None
    seerr_cache: Optional[dict] = None
    seerr_ext: str = ""
    queued_ids: Optional[set] = None
    ignored_ids: Optional[set] = None


# ─── Primitive operators ──────────────────────────────────────────────────────

def _eval_op(actual, op: str, expected) -> bool:
    if actual is None:
        return False
    try:
        if op == "gt":  return actual > expected
        if op == "gte": return actual >= expected
        if op == "lt":  return actual < expected
        if op == "lte": return actual <= expected
        if op == "eq":  return actual == expected
    except Exception:
        pass
    return False


def _days_since(dt: Optional[datetime]) -> Optional[int]:
    if not dt:
        return None
    return (now_utc() - dt).days


# ─── Library condition matching ───────────────────────────────────────────────

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
        op    = c.get("op", "gt")
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


# ─── Grace days + delete_at helpers ──────────────────────────────────────────

async def _get_seerr_grace(
    seerr_user_id: Optional[int], library_id: str, default_grace: int
) -> int:
    """Per-user grace days override (from seerr_user_rules)."""
    if not seerr_user_id:
        return default_grace
    async with get_db() as db:
        row = await db.fetch_one(
            "SELECT grace_days FROM seerr_user_rules "
            "WHERE seerr_user_id=? AND library_id=? AND enabled=1",
            (seerr_user_id, library_id),
        )
        return row["grace_days"] if row else default_grace


async def _update_delete_at_if_pending(
    emby_id: str,
    lib: dict,
    grace_days: int,
    title: str,
    *,
    last_played=None,
    play_count: int = 0,
):
    """Recalculate delete_at and refresh play data for an item already in queue.

    last_played and play_count are the CURRENT values from Emby, obtained during the
    scan that found the item already queued. Persisting them ensures the queue view
    shows accurate "last watched" data even when a user watched the item after it was
    originally queued (previously the snapshot was frozen at insertion time).
    """
    try:
        async with get_db() as db:
            row = await db.fetch_one(
                "SELECT id, detected_at, seerr_user_id, delete_at FROM media_queue "
                "WHERE emby_id=? AND status='pending'",
                (emby_id,),
            )
            if row:
                row_id = row["id"]
                row_detected_at = row["detected_at"]
                row_seerr_user_id = row["seerr_user_id"]
                row_delete_at = row["delete_at"]
                detected_at_dt = parse_iso_dt(row_detected_at) if row_detected_at else None
                if detected_at_dt:
                    effective_grace = await _get_seerr_grace(row_seerr_user_id, lib["id"], grace_days)
                    new_delete_at = detected_at_dt + timedelta(days=effective_grace)
                    old_dt = parse_iso_dt(row_delete_at) if row_delete_at else None
                    changed = old_dt is None or abs((new_delete_at - old_dt).total_seconds()) > 3600
                    lp_iso = last_played.isoformat() if last_played else None
                    await db.execute(
                        "UPDATE media_queue SET delete_at=?, last_played=?, view_count=? WHERE id=?",
                        (new_delete_at.isoformat(), lp_iso, play_count, row_id),
                    )
                    if changed:
                        # Reset threshold notifications so they fire again at the new deadline
                        await db.execute(
                            "DELETE FROM notifications WHERE media_id=? AND threshold NOT IN ('detected','now')",
                            (row_id,),
                        )
                    await db.commit()
                    if changed:
                        logger.debug(
                            f"delete_at recalculé ({effective_grace}j) : {title} "
                            f"→ {new_delete_at.strftime('%d/%m/%Y')} (notifs réinitialisées)"
                        )
    except Exception as e:
        logger.error(f"Erreur recalcul delete_at pour {title}: {e}")


# ─── Poster URL resolution ────────────────────────────────────────────────────

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

    try:
        emby_url, emby_key = await get_client(server_id)
        if emby_url and emby_key and emby_id:
            target = f"{emby_url}/Items/{emby_id}/Images/Primary?api_key={emby_key}&maxHeight=300"
            return f"/api/proxy/image?url={urllib.parse.quote(target, safe='')}"
    except Exception:
        pass

    return ""


# ─── Item evaluation ──────────────────────────────────────────────────────────

async def _evaluate_item(
    item: dict,
    lib: dict,
    conditions: list,
    logic: str,
    grace_days: int,
    user_ids: list,
    seerr_conditions: list,
    *,
    ctx: Optional[ScanContext] = None,
    user_data_cache: Optional[dict] = None,
    activity_log: Optional[dict] = None,
    radarr_cache: Optional[dict] = None,
    sonarr_cache: Optional[dict] = None,
    seerr_cache: Optional[dict] = None,
    seerr_ext: str = "",
    queued_ids: Optional[set] = None,
    ignored_ids: Optional[set] = None,
) -> Optional[dict]:
    """Evaluate a single Emby item; return queue-entry dict if eligible, else None.

    The caller (_scan_library) is responsible for DB insert and notifications.
    user_data_cache: {uid: {emby_id: UserData}}       — pre-fetched per library.
    radarr_cache:    {file_path: radarr_id}            — pre-fetched once per scan.
    sonarr_cache:    {episode_file_path: entry_dict}   — pre-fetched once per scan.
    seerr_cache:     {tmdb_id: {seerr_id, user_id, username}} — pre-fetched once per scan.
    queued_ids:      set of emby_ids already in media_queue   — pre-fetched once per scan.
    ignored_ids:     set of emby_ids in ignored_media         — pre-fetched once per scan.
    Falls back to individual DB/HTTP calls when caches are absent.
    Pass a ScanContext via `ctx` as a convenient alternative to the individual cache kwargs.
    """
    if ctx is not None:
        user_data_cache = user_data_cache if user_data_cache is not None else ctx.user_data_cache
        radarr_cache    = radarr_cache    if radarr_cache is not None    else ctx.radarr_cache
        sonarr_cache    = sonarr_cache    if sonarr_cache is not None    else ctx.sonarr_cache
        seerr_cache     = seerr_cache     if seerr_cache is not None     else ctx.seerr_cache
        seerr_ext       = seerr_ext       or ctx.seerr_ext
        queued_ids      = queued_ids      if queued_ids is not None      else ctx.queued_ids
        ignored_ids     = ignored_ids     if ignored_ids is not None     else ctx.ignored_ids

    emby_id    = item.get("Id")
    title      = item.get("Name") or "?"
    media_type = item.get("Type") or ""
    file_path  = item.get("Path") or ""
    tmdb_id    = str(item.get("ProviderIds", {}).get("Tmdb") or "")
    date_str   = item.get("DateCreated") or ""

    if not emby_id or not file_path:
        return None

    added_date = parse_iso_dt(date_str)
    if not added_date:
        return None

    # Aggregate UserData across users
    last_played: Optional[datetime] = None
    play_count = 0
    never_watched = True
    for uid in user_ids:
        if user_data_cache is not None:
            ud = user_data_cache.get(uid, {}).get(emby_id) or {}
        else:
            ud = await get_user_data(uid, emby_id) or {}
        pc     = ud.get("PlayCount") or 0
        played = bool(ud.get("Played"))
        # Emby returns Played=True with PlayCount=0 when items are manually marked
        # as watched (right-click → Mark played) without being played via the player.
        # Treat Played=True as play_count >= 1 so conditions and display are accurate.
        effective_pc = max(pc, 1) if played else pc
        play_count = max(play_count, effective_pc)
        if played or pc > 0:
            never_watched = False
            lp_str = ud.get("LastPlayedDate") or ""
            if not lp_str and activity_log is not None:
                # Fallback: activity log stores most-recent stop date (all users merged)
                lp_str = activity_log.get(emby_id) or ""
            if lp_str:
                lp = parse_iso_dt(lp_str)
                if lp and (last_played is None or lp > last_played):
                    last_played = lp

    if not _evaluate_conditions(conditions, logic, added_date, last_played, play_count, never_watched):
        return None

    # Skip if already in queue — but recalculate delete_at if grace changed
    if queued_ids is not None:
        if emby_id in queued_ids:
            await _update_delete_at_if_pending(
                emby_id, lib, grace_days, title,
                last_played=last_played, play_count=play_count,
            )
            return None
    else:
        async with get_db() as db:
            existing = await db.fetch_one(
                "SELECT id, status, detected_at, seerr_user_id FROM media_queue WHERE emby_id=?",
                (emby_id,),
            )
        if existing:
            row_id = existing["id"]
            row_status = existing["status"]
            row_detected_at = existing["detected_at"]
            row_seerr_user_id = existing["seerr_user_id"]
            if row_status == "pending" and row_detected_at:
                try:
                    detected_at_dt = parse_iso_dt(row_detected_at)
                    if detected_at_dt:
                        effective_grace = await _get_seerr_grace(row_seerr_user_id, lib["id"], grace_days)
                        new_delete_at = detected_at_dt + timedelta(days=effective_grace)
                        lp_iso = last_played.isoformat() if last_played else None
                        async with get_db() as db2:
                            await db2.execute(
                                "UPDATE media_queue SET delete_at=?, last_played=?, view_count=? WHERE id=?",
                                (new_delete_at.isoformat(), lp_iso, play_count, row_id),
                            )
                            await db2.commit()
                except Exception as e:
                    logger.error(f"Erreur recalcul delete_at pour {title}: {e}")
            return None

    if ignored_ids is not None:
        if emby_id in ignored_ids:
            return None
    else:
        async with get_db() as db:
            if await db.fetch_one("SELECT id FROM ignored_media WHERE emby_id=?", (emby_id,)):
                return None

    # Seerr lookup
    if seerr_cache is not None:
        seerr_data = seerr_cache.get(tmdb_id) if tmdb_id else None
    else:
        seerr_data = await seerr_find_request_by_tmdb(tmdb_id) if tmdb_id else None
    seerr_id       = seerr_data.get("seerr_id") if seerr_data else None
    seerr_user_id  = seerr_data.get("user_id") if seerr_data else None
    seerr_username = seerr_data.get("username", "") if seerr_data else ""

    # Seerr filter
    if seerr_conditions:
        has_includes = any(c.get("type") == "user_include" for c in seerr_conditions)
        has_excludes = any(c.get("type") == "user_exclude" for c in seerr_conditions)
        if has_includes and not seerr_user_id:
            await add_log("DEBUG", f"Ignoré (non demandé sur Seerr) : {title}", "scan")
            return None
        if not _seerr_filter_passes(seerr_user_id, seerr_conditions):
            reason = "exclu" if has_excludes else "non inclus"
            await add_log("DEBUG", f"Ignoré (utilisateur Seerr {reason}) : {title}", "scan")
            return None

    effective_grace = await _get_seerr_grace(seerr_user_id, lib["id"], grace_days)
    delete_at = now_utc() + timedelta(days=effective_grace)

    # Radarr/Sonarr IDs (from cache or individual HTTP call)
    radarr_id_val: Optional[int] = None
    sonarr_id_val: Optional[int] = None
    sonarr_series_id_val: Optional[int] = None
    season_number_val: Optional[int] = None
    if media_type == "Movie":
        radarr_id_val = (
            radarr_find_by_path_cached(file_path, radarr_cache)
            if radarr_cache is not None
            else await radarr_find_by_path(file_path)
        )
    else:
        sonarr_entry = sonarr_get_cache_entry(file_path, sonarr_cache) if sonarr_cache is not None else None
        if sonarr_entry:
            sonarr_id_val = sonarr_entry["ef_id"]
            sonarr_series_id_val = sonarr_entry["series_id"]
            season_number_val = sonarr_entry["season_number"]
        else:
            sonarr_id_val = await sonarr_find_by_path(file_path)

    poster_url = await _get_poster_url(
        emby_id, tmdb_id=tmdb_id, media_type=media_type,
        radarr_id=radarr_id_val, sonarr_id=sonarr_id_val,
    )

    seerr_request_url = ""
    if seerr_id and seerr_ext:
        path = "movie" if media_type == "Movie" else "tv"
        seerr_request_url = f"{seerr_ext.rstrip('/')}/{path}/{tmdb_id}"

    note = ""
    if seerr_username and effective_grace != grace_days:
        note = f" (règle Seerr {seerr_username}: {effective_grace}j)"
    await add_log("INFO", f"Éligible : {title} → {delete_at.strftime('%d/%m/%Y')}{note}", "scan")

    return {
        "emby_id": emby_id,
        "title": title,
        "media_type": media_type,
        "library_id": lib["id"],
        "library_name": lib["name"],
        "file_path": file_path,
        "poster_url": poster_url,
        "tmdb_id": tmdb_id,
        "seerr_id": seerr_id,
        "seerr_user_id": seerr_user_id,
        "seerr_username": seerr_username,
        "seerr_request_url": seerr_request_url,
        "radarr_id": radarr_id_val,
        "sonarr_id": sonarr_id_val,
        "sonarr_series_id": sonarr_series_id_val,
        "season_number": season_number_val,
        "detected_at": now_utc().isoformat(),
        "delete_at": delete_at.isoformat(),
        "added_date": added_date.isoformat(),
        "last_played": last_played.isoformat() if last_played else None,
        "view_count": play_count,
    }
