# backend/scanner/_plex_scanner.py
"""Plex library scanner — queues unwatched items past grace period."""
import logging
from dataclasses import dataclass
from datetime import timedelta

from ..plex_client import build_plex_client
from ..db.engine import get_db
from ..db.utils import now_utc
from ..db.repositories import (
    insert_queue_entry,
    get_queued_ids_for_server,
    get_pending_with_tmdb,
)
from ..db.logs import add_log
from ..logmsg import lm
from ._expert_rules import _build_plex_item_data, _evaluate_expert_rules
from ..rules.models import RuleAction as _RuleAction

logger = logging.getLogger(__name__)

_QUEUE_ACTION = _RuleAction.QUEUE.value


def _tmdb_key(tmdb_id: str, media_type: str) -> str:
    """Build a collision-free key: TMDB movies and TV shows share the same ID space.
    Prefix with normalised type so movie:1402 ≠ tv:1402.
    """
    t = (media_type or "").lower()
    if t in ("movie",):
        prefix = "movie"
    elif t in ("series", "episode", "season"):
        prefix = "tv"
    else:
        prefix = t
    return f"{prefix}_{tmdb_id}"


async def _load_ignored(plex_server_id: str) -> tuple[set, set]:
    """Return (ignored_ids, ignored_tmdb) for this scan, from a single connection.

    ignored_ids: emby_ids ignored in THIS Plex server's libraries only — same
    integer-collision risk as queued_ids (see _scan_plex_library).
    ignored_tmdb: cross-server TMDB ignore — content ignored on any server by
    TMDB ID is skipped on Plex too (e.g. ignored via Emby must not reappear).
    """
    async with get_db() as db:
        ignored_rows = await db.fetch_all(
            "SELECT DISTINCT im.emby_id FROM ignored_media im "
            "JOIN libraries l ON im.library_id = l.id "
            "WHERE l.server_id = ?",
            (plex_server_id,),
        )
        ign_tmdb_rows = await db.fetch_all(
            "SELECT tmdb_id, media_type FROM ignored_media WHERE tmdb_id != '' AND tmdb_id IS NOT NULL"
        )
    return {r["emby_id"] for r in ignored_rows}, _tmdb_key_set(ign_tmdb_rows)


def _tmdb_key_set(rows) -> set:
    """Set of "{type}_{tmdb_id}" keys for rows that have a TMDB id."""
    return {
        _tmdb_key(str(r["tmdb_id"]), str(r["media_type"] or ""))
        for r in rows if r["tmdb_id"]
    }


async def _grace_for_item(
    item: dict, tmdb_key: str, queued_tmdb: set, grace_days: int, lib_id: str
) -> tuple[bool, int]:
    """Return (should_queue, grace_days) for this item.

    Same content already queued by Emby (TMDB cross-reference) -> mirror it with
    the library grace. Otherwise an expert rule must say "queue" (its grace
    wins). No rule matched (including no-op "notify_only") -> skip: Plex has
    real addedAt dates that would make a generic fallback queue nearly everything.
    """
    if tmdb_key and tmdb_key in queued_tmdb:
        return True, grace_days
    action, rule_grace = await _evaluate_expert_rules(_build_plex_item_data(item), lib_id)
    return action == _QUEUE_ACTION, rule_grace


def _build_plex_entry(
    item: dict, *, lib_id: str, lib_name: str, tmdb_id: str, media_type: str,
    effective_grace: int, seerr_cache: dict | None,
) -> dict:
    """Assemble the queue entry (key order = insert column order), with Seerr
    enrichment looked up by TMDB ID in the shared cache."""
    detected_at = now_utc().isoformat()
    delete_at   = (now_utc() + timedelta(days=effective_grace)).isoformat()
    seerr_data  = (seerr_cache or {}).get(tmdb_id) if tmdb_id else None
    return {
        "emby_id":           item.get("plex_id"),
        "title":             item["title"],
        "media_type":        media_type,
        "library_id":        lib_id,
        "library_name":      lib_name,
        "file_path":         "",
        "poster_url":        item.get("poster_url", ""),
        "tmdb_id":           tmdb_id,
        "seerr_id":          seerr_data.get("seerr_id") if seerr_data else None,
        "seerr_user_id":     seerr_data.get("user_id") if seerr_data else None,
        "seerr_username":    seerr_data.get("username", "") if seerr_data else "",
        "seerr_request_url": seerr_data.get("request_url", "") if seerr_data else "",
        "radarr_id":         None,
        "sonarr_id":         None,
        "sonarr_series_id":  None,
        "arr_server_url":    None,
        "season_number":     item.get("season_number"),
        "detected_at":       detected_at,
        "delete_at":         delete_at,
        "added_date":        item.get("added_at"),
        "last_played":       item.get("last_viewed_at"),
        "view_count":        int(item.get("view_count") or 0),
    }


@dataclass(frozen=True)
class _PlexScanState:
    """Per-library scan inputs, loaded once before iterating the items."""
    queued_ids: set
    ignored_ids: set
    ignored_tmdb: set
    queued_tmdb: set
    grace_days: int
    lib_id: str
    lib_name: str
    seerr_cache: dict | None


async def _plex_item_to_entry(item: dict, st: _PlexScanState) -> dict | None:
    """Gate one Plex item; return its queue entry, or None to skip it.

    Gate order: no plex_id -> queued/ignored by id -> ignored by TMDB ->
    TMDB mirror or expert rule (see _grace_for_item).
    """
    plex_id = item.get("plex_id")
    if not plex_id or plex_id in st.queued_ids or plex_id in st.ignored_ids:
        return None

    tmdb_id    = str(item.get("tmdb_id") or "")
    media_type = item.get("media_type") or "movie"
    tmdb_key   = _tmdb_key(tmdb_id, media_type) if tmdb_id else ""
    if tmdb_key and tmdb_key in st.ignored_tmdb:
        return None

    should_queue, effective_grace = await _grace_for_item(
        item, tmdb_key, st.queued_tmdb, st.grace_days, st.lib_id
    )
    if not should_queue:
        return None
    return _build_plex_entry(
        item, lib_id=st.lib_id, lib_name=st.lib_name, tmdb_id=tmdb_id, media_type=media_type,
        effective_grace=effective_grace, seerr_cache=st.seerr_cache,
    )


async def _load_scan_state(
    plex_server_id: str, grace_days: int, lib_id: str, lib_name: str, seerr_cache: dict | None,
) -> _PlexScanState:
    """Load queued/ignored/TMDB sets (lookup order: queued ids, ignored, pending TMDB)."""
    # IMPORTANT: only include IDs from THIS Plex server's libraries.
    # Emby item IDs and Plex rating keys are both sequential integers
    # (range 1-2000+) and collide heavily. Using a global queued_ids set
    # would incorrectly filter out Plex items whose rating key matches
    # an Emby item ID, causing the scanner to find 0 results.
    queued_ids = await get_queued_ids_for_server(plex_server_id)
    ignored_ids, ignored_tmdb = await _load_ignored(plex_server_id)
    # TMDB cross-reference — keyed as "{type}_{tmdb_id}" to avoid collisions
    queued_tmdb = _tmdb_key_set(await get_pending_with_tmdb())
    return _PlexScanState(queued_ids, ignored_ids, ignored_tmdb, queued_tmdb,
                          grace_days, lib_id, lib_name, seerr_cache)


async def _scan_plex_library(*, server: dict, library: dict, seerr_cache: dict | None = None) -> int:
    """Scan one Plex library section and queue items that meet deletion criteria.

    Priority: expert rules are evaluated first.
    Fallback (when no expert rule matches): view_count == 0 + past grace_days cutoff.
    Returns count of items newly queued.
    """
    plex = build_plex_client(server)
    if plex is None:
        return 0

    section_id  = library["emby_library_id"]
    grace_days  = int(library.get("grace_days") or 7)
    lib_id      = library["id"]
    lib_name    = library["name"]
    server_name = server.get("name") or "Plex"

    items = await plex.scan_library(section_id)
    await add_log("INFO", lm("scan.lib_scan", prefix=f"{server_name} : ", name=lib_name), "scan")

    st = await _load_scan_state(str(server.get("id", "")), grace_days, lib_id, lib_name, seerr_cache)
    added = 0
    for item in items:
        entry = await _plex_item_to_entry(item, st)
        if entry is None:
            continue
        await insert_queue_entry(entry)
        added += 1

    # Always log the result (even 0) so operators can see Plex was scanned
    await add_log("INFO", lm("scan.lib_result", prefix=f"{server_name} : ", name=lib_name, n=added), "scan")
    return added
