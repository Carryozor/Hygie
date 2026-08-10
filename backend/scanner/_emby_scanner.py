# backend/scanner/_emby_scanner.py
"""Emby/Jellyfin library scanner and queue reevaluation."""
import asyncio
import json
import logging
from datetime import timedelta
from typing import Optional

import httpx

from ..db.utils import now_utc, parse_iso_dt, guarded_image_get
from ..db.engine import get_db
from ..db.settings_store import get_setting, get_bool_setting
from ..db.logs import add_log
from ..logmsg import lm

from ..emby_client import (
    get_client,
    get_items_in_library,
    get_library_user_data,
    get_play_activity,
    get_series_tmdb_map,
    get_user_data,  # noqa: F401 - unused directly, but mock.patch("backend.scanner._emby_scanner.get_user_data") targets require it re-imported here
    get_users,
    resolve_item_tmdb,
)
from ..arr_clients import radarr_find_by_path_cached, sonarr_get_cache_entry
from ..rules.legacy_conditions import (
    _aggregate_user_data,
    _evaluate_conditions,
    _evaluate_item,
    _get_poster_url,
)
from ..collection import sync_emby_collection
from ._queue_entry import _build_queue_entry, _insert_queue_entry
from ._expert_rules import _build_item_data, _evaluate_expert_rules
from ..db.repositories import (
    get_expert_rules as _get_expert_rules,
    get_pending_by_library,
    update_activity_log_batch,
    delete_by_id as _delete_queue_item,
)
from ._consolidation import _consolidate_and_insert
from ..rules.models import RuleAction as _RuleAction

logger = logging.getLogger(__name__)


async def _load_scan_caches(
    user_ids: list, emby_library_id, server_id: str, activity_log: Optional[dict]
) -> tuple[dict, list, dict]:
    """Build the per-scan user-data and expert-rules caches once, and resolve
    the activity log (fetching it if the orchestrator didn't pass one in).

    Returns (user_data_cache, expert_rules_cache, activity_log).
    """
    user_data_cache: dict = {}
    if user_ids:
        results = await asyncio.gather(*[
            get_library_user_data(uid, emby_library_id, server_id=server_id)
            for uid in user_ids
        ])
        user_data_cache = dict(zip(user_ids, results))

    # Load expert rules once per library scan — avoids N DB queries (one per item)
    expert_rules_cache = await _get_expert_rules(enabled_only=True)

    # Activity log: used as fallback for LastPlayedDate when Played=True but no date.
    # When the orchestrator passes activity_log (pre-fetched once per server),
    # we skip the fetch here. Otherwise we fetch it (single-library scan path).
    if activity_log is None:
        activity_log = {}
        try:
            activity_log = await get_play_activity(server_id=server_id, days=730)
        except Exception as _al_err:
            logger.warning("activity log fetch failed: %s", _al_err)

    return user_data_cache, expert_rules_cache, activity_log


async def _apply_activity_log_updates(activity_log: dict) -> None:
    """Direct update: write last_played + view_count for any pending queue entry
    that has a play record in the activity log — regardless of whether the item
    still meets deletion conditions. Must run BEFORE eligibility evaluation so
    the display is correct even if the conditions check short-circuits it.
    """
    if not activity_log:
        return
    try:
        params = [(_date, _eid, _date) for _eid, _date in activity_log.items() if _date]
        await update_activity_log_batch(params)
    except Exception as _upd_err:
        logger.warning("activity log DB update failed: %s", _upd_err)


async def _evaluate_expert_rules_fallback(
    item: dict, lib: dict, file_path: str, emby_id: str, *,
    user_ids: list, user_data_cache: dict, activity_log: dict,
    seerr_cache: Optional[dict], series_tmdb_map: Optional[dict],
    radarr_cache: Optional[dict], sonarr_cache: Optional[dict],
    seerr_ext_url: str, expert_rules_cache: list,
) -> Optional[dict]:
    """Fallback when _evaluate_item() found no match on the library's legacy
    conditions: try the library's expert rules instead. Returns a queue-entry
    dict to add (and logs the match), or None if nothing queues.
    """
    added_date = parse_iso_dt(item.get("DateCreated") or "")
    play_count, _, last_played = await _aggregate_user_data(
        user_ids, emby_id, user_data_cache, activity_log
    )

    seerr_user_id = None
    if seerr_cache is not None:
        tmdb_id    = resolve_item_tmdb(item, series_tmdb_map)
        seerr_data = seerr_cache.get(tmdb_id) if tmdb_id else None
        if seerr_data:
            seerr_user_id = seerr_data.get("user_id")

    item_data          = _build_item_data(item, play_count, last_played, added_date, seerr_user_id)
    action, rule_grace = await _evaluate_expert_rules(item_data, lib["id"], rules_cache=expert_rules_cache)

    if action != _RuleAction.QUEUE.value:
        return None

    tmdb_id            = resolve_item_tmdb(item, series_tmdb_map)
    media_type_item    = item.get("Type") or "Movie"
    seerr_data         = (seerr_cache or {}).get(tmdb_id) if tmdb_id else None
    seerr_id_val       = seerr_data.get("seerr_id") if seerr_data else None
    seerr_username_val = seerr_data.get("username", "") if seerr_data else ""

    radarr_id_val: Optional[int]        = None
    sonarr_id_val: Optional[int]        = None
    sonarr_series_id_val: Optional[int] = None
    season_number_val: Optional[int]    = None

    if media_type_item == "Movie":
        _radarr_result = radarr_find_by_path_cached(file_path, radarr_cache) if radarr_cache is not None else None
        # radarr_find_by_path_cached returns (radarr_id, url, key) tuple or None
        radarr_id_val = _radarr_result[0] if _radarr_result else None
    else:
        sonarr_entry = sonarr_get_cache_entry(file_path, sonarr_cache) if sonarr_cache is not None else None
        if sonarr_entry:
            sonarr_id_val        = sonarr_entry["ef_id"]
            sonarr_series_id_val = sonarr_entry["series_id"]
            season_number_val    = sonarr_entry["season_number"]

    poster_url_val = await _get_poster_url(
        emby_id, tmdb_id=tmdb_id, media_type=media_type_item,
        radarr_id=radarr_id_val, sonarr_id=sonarr_id_val,
    )
    seerr_request_url = ""
    if seerr_id_val and seerr_ext_url:
        path = "movie" if media_type_item == "Movie" else "tv"
        seerr_request_url = f"{seerr_ext_url.rstrip('/')}/{path}/{tmdb_id}"

    detect_at = now_utc()
    delete_at = detect_at + timedelta(days=rule_grace)
    expert_entry = _build_queue_entry(
        item, lib,
        detect_at=detect_at, delete_at=delete_at,
        added_date=added_date, last_played=last_played,
        poster_url=poster_url_val, tmdb_id=tmdb_id,
        seerr_id=seerr_id_val, seerr_user_id=seerr_user_id,
        seerr_username=seerr_username_val,
        seerr_request_url=seerr_request_url,
        radarr_id=radarr_id_val, sonarr_id=sonarr_id_val,
        sonarr_series_id=sonarr_series_id_val,
        season_number=season_number_val,
    )
    await add_log("INFO", lm("scan.expert_match", title=item.get('Name') or emby_id), "scan")
    return expert_entry


async def _collect_eligible_items(
    lib: dict, conditions: list, logic: str, grace_days: int, user_ids: list, seerr_conditions: list,
    emby_library_id, server_id: str, *,
    user_data_cache: dict, activity_log: dict,
    radarr_cache: Optional[dict], sonarr_cache: Optional[dict], seerr_cache: Optional[dict],
    queued_ids: Optional[set], ignored_ids: Optional[set],
    seerr_ext_url: str, expert_rules_cache: list,
) -> list:
    """Page through the library's items (500 at a time). Each item is checked
    against the library's legacy conditions first, then — on no match — its
    expert rules. Returns the list of queue-entry dicts eligible for insertion.
    """
    eligible: list = []
    start = 0
    # Episodes carry no series-level Tmdb id — map SeriesId → series tmdb so
    # Seerr matching works for series. Fetched lazily on the first episode
    # encountered: movie-only libraries never pay the extra HTTP call.
    series_tmdb_map: Optional[dict] = None
    # get_items_in_library() returns ([], 0) both when a library is genuinely
    # exhausted AND when a page fetch fails (circuit breaker open, timeout) —
    # it never raises. Track the last known total so a mid-scan failure can be
    # told apart from a real end-of-library and logged instead of silently
    # truncating the scan.
    last_known_total: Optional[int] = None

    while True:
        items, total = await get_items_in_library(
            emby_library_id, limit=500, start=start, server_id=server_id
        )
        if not items:
            if last_known_total is not None and start < last_known_total:
                logger.warning(
                    "Library %s scan stopped early at item %d/%d — a page fetch "
                    "failed (see get_items_in_library warning above); remaining "
                    "items will be picked up on the next scheduled scan.",
                    emby_library_id, start, last_known_total,
                )
            break
        last_known_total = total
        for item in items:
            if series_tmdb_map is None and (item.get("Type") or "") == "Episode":
                series_tmdb_map = await get_series_tmdb_map(emby_library_id, server_id)
            entry = await _evaluate_item(
                item, lib, conditions, logic, grace_days, user_ids, seerr_conditions,
                user_data_cache=user_data_cache,
                activity_log=activity_log,
                radarr_cache=radarr_cache,
                sonarr_cache=sonarr_cache,
                seerr_cache=seerr_cache,
                seerr_ext=seerr_ext_url,
                queued_ids=queued_ids,
                ignored_ids=ignored_ids,
                series_tmdb_map=series_tmdb_map,
            )
            if entry is not None:
                eligible.append(entry)
                continue

            emby_id   = item.get("Id")
            file_path = item.get("Path") or ""
            if not emby_id or not file_path:
                continue
            if queued_ids is not None and emby_id in queued_ids:
                continue
            if ignored_ids is not None and emby_id in ignored_ids:
                continue

            expert_entry = await _evaluate_expert_rules_fallback(
                item, lib, file_path, emby_id,
                user_ids=user_ids, user_data_cache=user_data_cache, activity_log=activity_log,
                seerr_cache=seerr_cache, series_tmdb_map=series_tmdb_map,
                radarr_cache=radarr_cache, sonarr_cache=sonarr_cache,
                seerr_ext_url=seerr_ext_url, expert_rules_cache=expert_rules_cache,
            )
            if expert_entry is not None:
                eligible.append(expert_entry)

        start += 500
        if start >= total:
            break

    return eligible


async def _insert_eligible_entries(
    lib: dict, eligible: list, deletion_unit: str, sonarr_cache: Optional[dict],
    queued_ids: Optional[set], dry_run: bool,
) -> int:
    if deletion_unit == "episode":
        added = 0
        for entry in eligible:
            await _insert_queue_entry(entry, queued_ids, dry_run)
            added += 1
        return added
    return await _consolidate_and_insert(
        lib, eligible, sonarr_cache or {}, deletion_unit, queued_ids, dry_run
    )


async def _scan_library(
    lib: dict,
    user_ids: list,
    server_id: str = "0",
    server_name: str = "",
    *,
    radarr_cache: Optional[dict] = None,
    sonarr_cache: Optional[dict] = None,
    seerr_cache: Optional[dict] = None,
    queued_ids: Optional[set] = None,
    ignored_ids: Optional[set] = None,
    activity_log: Optional[dict] = None,
) -> int:
    """Scan one Emby/Jellyfin library — returns count of items added.

    Caches and sets should be built once in the calling orchestrator and
    passed here to avoid redundant HTTP/DB calls.
    activity_log: pre-fetched {item_id: last_stop_date_iso} from the Emby activity
    log. When None, the scanner fetches it internally (backward compatibility).
    """
    conditions       = json.loads(lib.get("conditions") or "[]")
    logic            = lib.get("logic") or "AND"
    grace_days       = lib.get("grace_days") or 7
    seerr_conditions = json.loads(lib.get("seerr_conditions") or "[]")
    emby_library_id  = lib["emby_library_id"]
    deletion_unit    = lib.get("deletion_unit") or "episode"

    prefix = f"{server_name} : " if server_name else ""
    await add_log("INFO", lm("scan.lib_scan", prefix=prefix, name=lib['name']), "scan")

    user_data_cache, expert_rules_cache, activity_log = await _load_scan_caches(
        user_ids, emby_library_id, server_id, activity_log
    )
    await _apply_activity_log_updates(activity_log)

    seerr_ext_url: str = await get_setting("seerr_external_url") or ""
    dry_run = await get_bool_setting("dry_run")

    eligible = await _collect_eligible_items(
        lib, conditions, logic, grace_days, user_ids, seerr_conditions,
        emby_library_id, server_id,
        user_data_cache=user_data_cache, activity_log=activity_log,
        radarr_cache=radarr_cache, sonarr_cache=sonarr_cache, seerr_cache=seerr_cache,
        queued_ids=queued_ids, ignored_ids=ignored_ids,
        seerr_ext_url=seerr_ext_url, expert_rules_cache=expert_rules_cache,
    )

    added = await _insert_eligible_entries(lib, eligible, deletion_unit, sonarr_cache, queued_ids, dry_run)

    await add_log("INFO", lm("scan.lib_result", prefix=prefix, name=lib['name'], n=added), "scan")
    return added


async def reevaluate_library_queue(library_id: str) -> int:
    """Recheck conditions for pending items in a library. Remove those no longer matching."""
    async with get_db() as db:
        lib = await db.fetch_one("SELECT * FROM libraries WHERE id=?", (library_id,))
        if not lib:
            return 0

    pending = await get_pending_by_library(library_id)
    if not pending:
        return 0

    conditions = json.loads(lib.get("conditions") or "[]")
    logic      = lib.get("logic") or "AND"
    server_id  = str(lib.get("server_id") or "0")
    users      = await get_users(server_id=server_id)
    user_ids   = [u["Id"] for u in users] if users else []
    removed    = 0

    # Batch-fetch user data for ALL users in one pass — avoids N×M sequential HTTP calls.
    # Previously this was: for uid in user_ids: ud = await get_user_data(uid, emby_id)
    # which caused N_items × N_users HTTP requests (e.g. 100 items × 3 users = 300 calls).
    user_data_cache: dict = {}
    if user_ids and pending:
        emby_library_id = lib.get("emby_library_id") or library_id
        results = await asyncio.gather(*[
            get_library_user_data(uid, emby_library_id, server_id=server_id)
            for uid in user_ids
        ])
        user_data_cache = dict(zip(user_ids, results))

    for row in pending:
        emby_id    = row["emby_id"]
        added_date = parse_iso_dt(row.get("added_date"))

        play_count, never_watched, last_played = await _aggregate_user_data(
            user_ids, emby_id, user_data_cache, None
        )
        # Merge with DB-stored last_played (may predate the current user-data cache)
        raw_lp = parse_iso_dt(row.get("last_played"))
        if raw_lp:
            never_watched = False
            if last_played is None or raw_lp > last_played:
                last_played = raw_lp

        if not _evaluate_conditions(conditions, logic, added_date, last_played, play_count, never_watched):
            poster_url = row.get("poster_url", "")
            if poster_url and poster_url.startswith("http") and emby_id:
                try:
                    _lib_srv = str(lib.get("server_id") or "0")
                    emby_url_val, emby_key_val = await get_client(_lib_srv)
                    overlay_on = await get_bool_setting("emby_leaving_soon_overlay")
                    if overlay_on:
                        poster_bytes = await guarded_image_get(poster_url, timeout=10)
                        if poster_bytes:
                            async with httpx.AsyncClient(timeout=10) as hc:
                                await hc.post(
                                    f"{emby_url_val}/Items/{emby_id}/Images/Primary",
                                    headers={"X-Emby-Token": emby_key_val, "Content-Type": "image/jpeg"},
                                    content=poster_bytes,
                                )
                except Exception as e_rp:
                    logger.debug(f"Restore poster (reevaluate): {e_rp}")

            await _delete_queue_item(row["id"])
            removed += 1
            await add_log("INFO", lm("scan.removed_conditions", title=row['title']), "scan")

    if removed:
        await add_log("INFO", lm("scan.reevaluated", name=lib['name'], n=removed), "scan")
        await sync_emby_collection()
    return removed
