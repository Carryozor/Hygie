# backend/scanner/_plex_scanner.py
"""Plex library scanner — queues unwatched items past grace period."""
import logging
from datetime import timedelta

from ..plex_client import build_plex_client
from ..db.engine import get_db
from ..db.utils import now_utc, parse_iso_dt
from ..db.repositories import insert_queue_entry
from ..db.logs import add_log
from ..logmsg import lm
from ._expert_rules import _build_plex_item_data, _evaluate_expert_rules
from ..rules.models import RuleAction as _RuleAction

logger = logging.getLogger(__name__)

_QUEUE_ACTION = _RuleAction.QUEUE.value


async def _scan_plex_library(*, server: dict, library: dict) -> int:
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

    cutoff = now_utc() - timedelta(days=grace_days)
    added  = 0

    async with get_db() as db:
        queued_rows  = await db.fetch_all("SELECT emby_id FROM media_queue")
        queued_ids   = {r["emby_id"] for r in queued_rows}
        ignored_rows = await db.fetch_all("SELECT emby_id FROM ignored_media")
        ignored_ids  = {r["emby_id"] for r in ignored_rows}

    for item in items:
        plex_id = item.get("plex_id")
        if not plex_id:
            continue
        if plex_id in queued_ids or plex_id in ignored_ids:
            continue

        added_at = parse_iso_dt(item.get("added_at") or "")

        # ── Expert rules evaluation ────────────────────────────────────────────
        item_data = _build_plex_item_data(item)
        action, rule_grace = await _evaluate_expert_rules(item_data, lib_id)

        if action == _QUEUE_ACTION:
            # Expert rule matched — use rule's grace period
            effective_grace = rule_grace
        elif action is not None:
            # Rule matched but action is not "queue" (e.g. notify_only) — skip
            continue
        else:
            # No expert rule matched — fallback: unwatched + past cutoff
            if int(item.get("view_count") or 0) > 0:
                continue
            if added_at is None or added_at > cutoff:
                continue
            effective_grace = grace_days

        detected_at = now_utc().isoformat()
        delete_at   = (now_utc() + timedelta(days=effective_grace)).isoformat()
        added_date  = item.get("added_at")

        entry = {
            "emby_id":           plex_id,
            "title":             item["title"],
            "media_type":        item["media_type"],
            "library_id":        lib_id,
            "library_name":      lib_name,
            "file_path":         "",
            "poster_url":        item.get("poster_url", ""),
            "tmdb_id":           item.get("tmdb_id", ""),
            "seerr_id":          None,
            "seerr_user_id":     None,
            "seerr_username":    "",
            "seerr_request_url": "",
            "radarr_id":         None,
            "sonarr_id":         None,
            "sonarr_series_id":  None,
            "season_number":     item.get("season_number"),
            "detected_at":       detected_at,
            "delete_at":         delete_at,
            "added_date":        added_date,
            "last_played":       item.get("last_viewed_at"),
        }
        await insert_queue_entry(entry)
        added += 1

    if added:
        await add_log("INFO", lm("scan.lib_result", prefix=f"{server_name} : ", name=lib_name, n=added), "scan")
    return added
