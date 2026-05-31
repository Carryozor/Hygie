# backend/scanner/_consolidation.py
"""Consolidate eligible episodes into season/series queue entries."""
from collections import defaultdict
from typing import Optional

from ._queue_entry import _insert_queue_entry


async def _consolidate_and_insert(
    lib: dict,
    eligible: list,
    sonarr_cache: dict,
    deletion_unit: str,
    queued_ids: Optional[set],
    dry_run: bool,
) -> int:
    """Group eligible episodes by season or series; insert one entry per complete group.

    Only groups where ALL episode files are eligible get inserted.
    Returns count of consolidated entries added.
    """
    season_totals: dict = defaultdict(int)
    series_totals: dict = defaultdict(int)
    for cache_entry in sonarr_cache.values():
        if not isinstance(cache_entry, dict):
            continue
        sid = cache_entry.get("series_id")
        sn  = cache_entry.get("season_number")
        if sid is not None and sn is not None:
            season_totals[(sid, sn)] += 1
            series_totals[sid] += 1

    if deletion_unit == "season":
        groups: dict = defaultdict(list)
        for ep in eligible:
            sid = ep.get("sonarr_series_id")
            sn  = ep.get("season_number")
            if sid is not None and sn is not None:
                groups[(sid, sn)].append(ep)

        added = 0
        for (sid, sn), eps in groups.items():
            total = season_totals.get((sid, sn), 0)
            if total > 0 and len(eps) >= total:
                anchor       = max(eps, key=lambda e: e["delete_at"])
                series_title = (sonarr_cache.get(anchor["file_path"]) or {}).get(
                    "series_title", anchor["title"]
                )
                poster = (sonarr_cache.get(anchor["file_path"]) or {}).get("poster_url", "")
                consolidated = {
                    **anchor,
                    "emby_id":         f"sonarr-season:{sid}:{sn}",
                    "title":           f"{series_title} — Saison {sn}",
                    "sonarr_id":       None,
                    "sonarr_series_id": sid,
                    "season_number":   sn,
                    "poster_url":      poster or anchor["poster_url"],
                    "file_path":       anchor["file_path"],
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
                anchor      = max(eps, key=lambda e: e["delete_at"])
                cache_entry = next(
                    (v for v in sonarr_cache.values()
                     if isinstance(v, dict) and v.get("series_id") == sid),
                    {}
                )
                series_title = cache_entry.get("series_title", anchor["title"])
                poster        = cache_entry.get("poster_url", "")
                consolidated  = {
                    **anchor,
                    "emby_id":         f"sonarr-series:{sid}",
                    "title":           series_title,
                    "sonarr_id":       None,
                    "sonarr_series_id": sid,
                    "season_number":   None,
                    "poster_url":      poster or anchor["poster_url"],
                    "file_path":       anchor["file_path"],
                }
                if queued_ids is None or consolidated["emby_id"] not in queued_ids:
                    await _insert_queue_entry(consolidated, queued_ids, dry_run)
                    added += 1
        return added

    return 0
