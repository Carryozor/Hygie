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

    cutoff = now_utc() - timedelta(days=grace_days)
    added  = 0

    plex_server_id = str(server.get("id", ""))
    async with get_db() as db:
        # IMPORTANT: only include IDs from THIS Plex server's libraries.
        # Emby item IDs and Plex rating keys are both sequential integers
        # (range 1-2000+) and collide heavily. Using a global queued_ids set
        # would incorrectly filter out Plex items whose rating key matches
        # an Emby item ID, causing the scanner to find 0 results.
        queued_rows  = await db.fetch_all(
            """SELECT mq.emby_id FROM media_queue mq
               JOIN libraries l ON mq.library_id = l.id
               WHERE l.server_id = ?""",
            (plex_server_id,),
        )
        queued_ids   = {r["emby_id"] for r in queued_rows}

        # Filter ignored_media to THIS server only — same integer collision risk as queued_ids.
        # Items ignored on other servers have different numeric IDs that could collide.
        ignored_rows = await db.fetch_all(
            """SELECT DISTINCT im.emby_id FROM ignored_media im
               JOIN libraries l ON im.library_id = l.id
               WHERE l.server_id = ?""",
            (plex_server_id,),
        )
        ignored_ids  = {r["emby_id"] for r in ignored_rows}

        # TMDB cross-reference — keyed as "{type}_{tmdb_id}" to avoid collisions
        # (TMDB uses separate ID spaces for movies and TV shows: movie:1402 ≠ tv:1402)
        tmdb_rows    = await db.fetch_all(
            "SELECT tmdb_id, media_type FROM media_queue WHERE status='pending' AND tmdb_id != '' AND tmdb_id IS NOT NULL"
        )
        queued_tmdb  = {
            _tmdb_key(str(r["tmdb_id"]), str(r["media_type"] or ""))
            for r in tmdb_rows if r["tmdb_id"]
        }

        # Cross-server TMDB ignore: if this content is ignored on any server by TMDB ID,
        # skip it on Plex too (e.g. content ignored via Emby should not reappear via Plex).
        ign_tmdb_rows = await db.fetch_all(
            "SELECT tmdb_id, media_type FROM ignored_media WHERE tmdb_id != '' AND tmdb_id IS NOT NULL"
        )
        ignored_tmdb  = {
            _tmdb_key(str(r["tmdb_id"]), str(r["media_type"] or ""))
            for r in ign_tmdb_rows if r["tmdb_id"]
        }

    for item in items:
        plex_id = item.get("plex_id")
        if not plex_id:
            continue
        if plex_id in queued_ids or plex_id in ignored_ids:
            continue

        tmdb_id    = str(item.get("tmdb_id") or "")
        media_type = item.get("media_type") or "movie"
        tmdb_key   = _tmdb_key(tmdb_id, media_type) if tmdb_id else ""

        # Cross-server TMDB ignore: content ignored on any server blocks Plex too
        if tmdb_key and tmdb_key in ignored_tmdb:
            continue

        added_at = parse_iso_dt(item.get("added_at") or "")

        # ── Cross-server TMDB check ────────────────────────────────────────────
        if tmdb_key and tmdb_key in queued_tmdb:
            # Same content already queued by Emby — mirror to Plex
            effective_grace = grace_days

        else:
            # ── Expert rules evaluation ──────────────────────────────────────────
            item_data = _build_plex_item_data(item)
            action, rule_grace = await _evaluate_expert_rules(item_data, lib_id)

            if action == _QUEUE_ACTION:
                effective_grace = rule_grace
            else:
                # No rule matched (including no-op "notify_only") → skip.
                # Plex has real addedAt dates that would cause a generic fallback
                # to queue nearly everything. Only queue via TMDB cross-reference
                # (above) or an explicit expert rule match.
                continue

        detected_at = now_utc().isoformat()
        delete_at   = (now_utc() + timedelta(days=effective_grace)).isoformat()
        added_date  = item.get("added_at")

        # Seerr enrichment — look up by TMDB ID in the shared cache
        seerr_data     = (seerr_cache or {}).get(tmdb_id) if tmdb_id else None
        seerr_id_val   = seerr_data.get("seerr_id")    if seerr_data else None
        seerr_uid      = seerr_data.get("user_id")     if seerr_data else None
        seerr_uname    = seerr_data.get("username", "") if seerr_data else ""
        seerr_req_url  = seerr_data.get("request_url", "") if seerr_data else ""

        entry = {
            "emby_id":           plex_id,
            "title":             item["title"],
            "media_type":        media_type,
            "library_id":        lib_id,
            "library_name":      lib_name,
            "file_path":         "",
            "poster_url":        item.get("poster_url", ""),
            "tmdb_id":           tmdb_id,
            "seerr_id":          seerr_id_val,
            "seerr_user_id":     seerr_uid,
            "seerr_username":    seerr_uname,
            "seerr_request_url": seerr_req_url,
            "radarr_id":         None,
            "sonarr_id":         None,
            "sonarr_series_id":  None,
            "season_number":     item.get("season_number"),
            "detected_at":       detected_at,
            "delete_at":         delete_at,
            "added_date":        added_date,
            "last_played":       item.get("last_viewed_at"),
            "view_count":        int(item.get("view_count") or 0),
        }
        await insert_queue_entry(entry)
        added += 1

    # Always log the result (even 0) so operators can see Plex was scanned
    await add_log("INFO", lm("scan.lib_result", prefix=f"{server_name} : ", name=lib_name, n=added), "scan")
    return added
