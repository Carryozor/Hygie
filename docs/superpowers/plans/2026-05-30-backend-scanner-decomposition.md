# Backend Scanner Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the 794-line `backend/scanner.py` monolith into a focused `backend/scanner/` package with one-responsibility modules, and eliminate the circular-import workaround (`from .plex_client import ...` inside function bodies).

**Architecture:** Convert `scanner.py` → `scanner/` package. The public API (`run_scan`, `run_scan_library`, `reevaluate_library_queue`) is re-exported from `scanner/__init__.py` so every existing import (`from .scanner import run_scan`) keeps working without changes. Internal logic is split by responsibility: queue entry building, expert-rule evaluation, season/series consolidation, Emby scanning, Plex scanning, orchestration.

**Tech Stack:** Python 3.11, FastAPI, asyncio, pytest — run `pytest backend/tests/ -v` to confirm no regressions.

---

## File Structure

```
backend/scanner/                    ← new package (replaces scanner.py)
  __init__.py                       ← re-exports run_scan, run_scan_library, reevaluate_library_queue
  _queue_entry.py                   ← _build_queue_entry(), _insert_queue_entry()
  _expert_rules.py                  ← _build_item_data(), _evaluate_expert_rules()
  _consolidation.py                 ← _consolidate_and_insert()
  _emby_scanner.py                  ← _scan_library(), reevaluate_library_queue()
  _plex_scanner.py                  ← _scan_plex_library() (no more import-inside-function)
  _orchestrator.py                  ← run_scan(), run_scan_library()
backend/scanner.py                  ← DELETED after Task 8
```

**Import chain after refactor:**
```
scheduler.py  →  scanner/__init__.py  →  _orchestrator.py  →  _emby_scanner.py / _plex_scanner.py
                                      →  _emby_scanner.py  →  _queue_entry.py, _expert_rules.py, _consolidation.py
routers/libraries.py  →  scheduler.py  (unchanged)
```

---

### Task 1: Create scanner/ package skeleton

**Files:**
- Create: `backend/scanner/__init__.py` (stub — will be filled in Task 8)
- Create: `backend/scanner/_queue_entry.py`

Start with the two pure helper functions that have zero internal dependencies on other scanner functions.

- [ ] **Step 1: Create the package directory and stub `__init__.py`**

```python
# backend/scanner/__init__.py
"""scanner package — public API re-exported here for backward compatibility."""
# Populated in Task 8 after all submodules are created.
```

- [ ] **Step 2: Create `_queue_entry.py`**

Copy lines 60–104 and 509–522 verbatim from `backend/scanner.py`.

```python
# backend/scanner/_queue_entry.py
"""Build and insert media_queue entries."""
import logging
from typing import Optional

from ..db.repositories import insert_queue_entry, mark_notified_detected
from ..notifications import _send_pending_notifications
from ..discord_client import send_notification

logger = logging.getLogger(__name__)


def _build_queue_entry(
    item: dict,
    lib: dict,
    *,
    detect_at,
    delete_at,
    added_date,
    last_played,
    poster_url: str = "",
    tmdb_id: str = "",
    seerr_id=None,
    seerr_user_id=None,
    seerr_username: str = "",
    seerr_request_url: str = "",
    radarr_id=None,
    sonarr_id=None,
    sonarr_series_id=None,
    season_number=None,
) -> dict:
    """Build a media_queue entry dict from an Emby/Plex item and enrichment data."""
    return {
        "emby_id":           item.get("Id", ""),
        "title":             item.get("Name") or "?",
        "media_type":        item.get("Type") or "Movie",
        "library_id":        lib["id"],
        "library_name":      lib["name"],
        "file_path":         item.get("Path") or "",
        "poster_url":        poster_url,
        "tmdb_id":           tmdb_id,
        "seerr_id":          seerr_id,
        "seerr_user_id":     seerr_user_id,
        "seerr_username":    seerr_username,
        "seerr_request_url": seerr_request_url,
        "radarr_id":         radarr_id,
        "sonarr_id":         sonarr_id,
        "sonarr_series_id":  sonarr_series_id,
        "season_number":     season_number,
        "detected_at":       detect_at.isoformat(),
        "delete_at":         delete_at.isoformat(),
        "added_date":        added_date.isoformat() if added_date else detect_at.isoformat(),
        "last_played":       last_played.isoformat() if last_played else None,
    }


async def _insert_queue_entry(
    entry: dict,
    queued_ids: Optional[set],
    dry_run: bool,
) -> None:
    """Insert one eligible item into media_queue and send detected notification."""
    await insert_queue_entry(entry)
    if queued_ids is not None:
        queued_ids.add(entry["emby_id"])
    item_notif = {k: entry[k] for k in (
        "title", "media_type", "library_name",
        "seerr_user_id", "seerr_username",
        "poster_url", "delete_at",
    )}
    try:
        sent = await send_notification([item_notif], "detected", dry_run=dry_run)
        if sent:
            await mark_notified_detected(entry["emby_id"])
    except Exception as e:
        logger.warning(f"Detected notification failed for {entry['title']}: {e}")
```

- [ ] **Step 3: Verify package is importable**

```bash
cd /opt/claude/hygie && python -c "from backend.scanner._queue_entry import _build_queue_entry, _insert_queue_entry; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git -C /opt/claude/hygie add backend/scanner/
git -C /opt/claude/hygie commit -m "refactor(scanner): create scanner/ package, extract _queue_entry.py"
```

---

### Task 2: Extract _expert_rules.py

**Files:**
- Create: `backend/scanner/_expert_rules.py`

Lines 106–165 of scanner.py verbatim.

- [ ] **Step 1: Create the module**

```python
# backend/scanner/_expert_rules.py
"""Build item_data dict and evaluate expert rules against it."""
from typing import Optional

from ..db.utils import now_utc
from ..db.repositories import get_expert_rules as _get_expert_rules
from ..rules.engine import evaluate_rule as _evaluate_rule
from ..rules.models import RuleAction as _RuleAction


def _build_item_data(
    item: dict,
    play_count: int,
    last_played,
    added_date,
    seerr_user_id=None,
) -> dict:
    """Build the item_data dict expected by evaluate_rule.

    Keys match ConditionField enum values:
    days_not_watched, play_count, rating, file_size_gb, added_days_ago,
    media_type, seerr_user_id.
    """
    now = now_utc()

    if last_played is not None:
        days_not_watched = (now - last_played).days
    elif added_date is not None:
        days_not_watched = (now - added_date).days
    else:
        days_not_watched = 0

    added_days_ago = (now - added_date).days if added_date is not None else 0
    rating = float(item.get("CommunityRating") or 0.0)

    size_bytes = 0
    media_sources = item.get("MediaSources") or []
    if media_sources and isinstance(media_sources[0], dict):
        size_bytes = int(media_sources[0].get("Size") or 0)
    file_size_gb = round(size_bytes / (1024 ** 3), 4) if size_bytes else 0.0

    return {
        "days_not_watched": days_not_watched,
        "play_count":       play_count,
        "rating":           rating,
        "file_size_gb":     file_size_gb,
        "added_days_ago":   added_days_ago,
        "media_type":       item.get("Type") or "Movie",
        "seerr_user_id":    seerr_user_id,
    }


async def _evaluate_expert_rules(item_data: dict, library_id=None) -> Optional[str]:
    """Return action string ('queue'/'notify_only') if any enabled expert rule matches, else None.

    Rules with library_id=None apply globally; rules scoped to a library_id only apply there.
    """
    rules = await _get_expert_rules(enabled_only=True)
    for rule in rules:
        if rule.library_id is not None and library_id is not None:
            if str(rule.library_id) != str(library_id):
                continue
        if _evaluate_rule(rule, item_data):
            return rule.action.value
    return None
```

- [ ] **Step 2: Verify importable**

```bash
cd /opt/claude/hygie && python -c "from backend.scanner._expert_rules import _build_item_data, _evaluate_expert_rules; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Run existing tests**

```bash
cd /opt/claude/hygie && python -m pytest backend/tests/ -v 2>&1 | tail -10
```
Expected: all tests pass (tests don't import from scanner, so no regressions possible here).

- [ ] **Step 4: Commit**

```bash
git -C /opt/claude/hygie add backend/scanner/_expert_rules.py
git -C /opt/claude/hygie commit -m "refactor(scanner): extract _expert_rules.py"
```

---

### Task 3: Extract _consolidation.py

**Files:**
- Create: `backend/scanner/_consolidation.py`

Lines 525–617 of scanner.py verbatim.

- [ ] **Step 1: Create the module**

```python
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
                anchor       = max(eps, key=lambda e: e["delete_at"])
                cache_entry  = next(
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
```

- [ ] **Step 2: Verify importable**

```bash
cd /opt/claude/hygie && python -c "from backend.scanner._consolidation import _consolidate_and_insert; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git -C /opt/claude/hygie add backend/scanner/_consolidation.py
git -C /opt/claude/hygie commit -m "refactor(scanner): extract _consolidation.py"
```

---

### Task 4: Extract _plex_scanner.py

**Files:**
- Create: `backend/scanner/_plex_scanner.py`

Lines 715–794 of scanner.py. Crucially, `from .plex_client import build_plex_client` is now a **top-level import** — the circular-import workaround (import inside function body) is eliminated.

- [ ] **Step 1: Create the module**

```python
# backend/scanner/_plex_scanner.py
"""Plex library scanner — queues unwatched items past grace period."""
import logging
from datetime import timedelta

from ..plex_client import build_plex_client
from ..db.engine import get_db
from ..db.utils import now_utc, parse_iso_dt
from ..db.repositories import insert_queue_entry
from ..db.logs import add_log

logger = logging.getLogger(__name__)


async def _scan_plex_library(*, server: dict, library: dict) -> int:
    """Scan one Plex library section and queue items that meet deletion criteria.

    Uses view_count==0 as the primary condition. Items added before grace_days
    cutoff and never watched are queued as 'pending'.
    Returns count of items newly queued.
    """
    plex = build_plex_client(server)
    if plex is None:
        return 0

    section_id = library["emby_library_id"]
    grace_days = int(library.get("grace_days") or 7)
    lib_id     = library["id"]
    lib_name   = library["name"]

    items = await plex.scan_library(section_id)
    await add_log("INFO", f"Scan Plex : {lib_name} — {len(items)} éléments", "scan")

    cutoff = now_utc() - timedelta(days=grace_days)
    added  = 0

    async with get_db() as db:
        queued_rows  = await db.fetch_all("SELECT emby_id FROM media_queue")
        queued_ids   = {r["emby_id"] for r in queued_rows}
        ignored_rows = await db.fetch_all("SELECT emby_id FROM ignored_media")
        ignored_ids  = {r["emby_id"] for r in ignored_rows}

    for item in items:
        plex_id = item["plex_id"]
        if not plex_id:
            continue
        if plex_id in queued_ids or plex_id in ignored_ids:
            continue
        if item["view_count"] > 0:
            continue

        added_at_str = item.get("added_at")
        if not added_at_str:
            continue
        added_at = parse_iso_dt(added_at_str)
        if added_at is None or added_at > cutoff:
            continue

        detected_at = now_utc().isoformat()
        delete_at   = (now_utc() + timedelta(days=grace_days)).isoformat()

        entry = {
            "emby_id":           plex_id,
            "title":             item["title"],
            "media_type":        item["media_type"],
            "library_id":        lib_id,
            "library_name":      lib_name,
            "file_path":         "",
            "poster_url":        item["poster_url"],
            "tmdb_id":           item["tmdb_id"],
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
            "added_date":        item.get("added_at"),
            "last_played":       item.get("last_viewed_at"),
        }
        await insert_queue_entry(entry)
        added += 1

    if added:
        await add_log("INFO", f"Plex {lib_name} : {added} média(s) mis en file", "scan")
    return added
```

- [ ] **Step 2: Verify importable (confirms no circular import)**

```bash
cd /opt/claude/hygie && python -c "from backend.scanner._plex_scanner import _scan_plex_library; print('OK')"
```
Expected: `OK` (no `ImportError`)

- [ ] **Step 3: Commit**

```bash
git -C /opt/claude/hygie add backend/scanner/_plex_scanner.py
git -C /opt/claude/hygie commit -m "refactor(scanner): extract _plex_scanner.py, fix circular import"
```

---

### Task 5: Extract _emby_scanner.py

**Files:**
- Create: `backend/scanner/_emby_scanner.py`

Lines 348–506 (`_scan_library`) and 621–712 (`reevaluate_library_queue`) of scanner.py.

- [ ] **Step 1: Create the module**

```python
# backend/scanner/_emby_scanner.py
"""Emby/Jellyfin library scanner and queue reevaluation."""
import asyncio
import base64
import json
import logging
from datetime import timedelta
from typing import Optional

import httpx

from ..db.utils import now_utc, parse_iso_dt
from ..db.engine import get_db
from ..db.settings_store import get_setting, get_bool_setting
from ..db.logs import add_log
from ..emby_client import (
    get_client,
    get_items_in_library,
    get_library_user_data,
    get_user_data,
    get_users,
)
from ..arr_clients import radarr_find_by_path_cached, sonarr_get_cache_entry
from ..conditions import _evaluate_conditions, _evaluate_item, _get_poster_url
from ..collection import sync_emby_collection
from ._queue_entry import _build_queue_entry, _insert_queue_entry
from ._expert_rules import _build_item_data, _evaluate_expert_rules
from ._consolidation import _consolidate_and_insert
from ..rules.models import RuleAction as _RuleAction

logger = logging.getLogger(__name__)


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
    """Scan one Emby/Jellyfin library — returns count of items added.

    Caches and sets should be built once in the calling orchestrator and
    passed here to avoid redundant HTTP/DB calls.
    """
    conditions     = json.loads(lib.get("conditions") or "[]")
    logic          = lib.get("logic") or "AND"
    grace_days     = lib.get("grace_days") or 7
    seerr_conditions = json.loads(lib.get("seerr_conditions") or "[]")
    emby_library_id  = lib["emby_library_id"]
    deletion_unit    = lib.get("deletion_unit") or "episode"

    added = 0
    start = 0
    await add_log("INFO", f"Scan : {lib['name']}", "scan")

    user_data_cache: dict = {}
    if user_ids:
        results = await asyncio.gather(*[
            get_library_user_data(uid, emby_library_id, server_id=server_id)
            for uid in user_ids
        ])
        user_data_cache = dict(zip(user_ids, results))

    seerr_ext_url: str = await get_setting("seerr_external_url") or ""
    dry_run = await get_bool_setting("dry_run")

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
            else:
                emby_id   = item.get("Id")
                file_path = item.get("Path") or ""
                if not emby_id or not file_path:
                    continue
                if queued_ids is not None and emby_id in queued_ids:
                    continue
                if ignored_ids is not None and emby_id in ignored_ids:
                    continue

                play_count  = 0
                last_played = None
                added_date  = parse_iso_dt(item.get("DateCreated") or "")
                for uid in user_ids:
                    ud = (user_data_cache.get(uid) or {}).get(emby_id) or {}
                    pc = ud.get("PlayCount") or 0
                    play_count = max(play_count, pc)
                    lp_str = ud.get("LastPlayedDate") or ""
                    if lp_str:
                        lp = parse_iso_dt(lp_str)
                        if lp and (last_played is None or lp > last_played):
                            last_played = lp

                seerr_user_id = None
                if seerr_cache is not None:
                    tmdb_id    = str(item.get("ProviderIds", {}).get("Tmdb") or "")
                    seerr_data = seerr_cache.get(tmdb_id) if tmdb_id else None
                    if seerr_data:
                        seerr_user_id = seerr_data.get("user_id")

                item_data = _build_item_data(item, play_count, last_played, added_date, seerr_user_id)
                action    = await _evaluate_expert_rules(item_data, lib["id"])

                if action == _RuleAction.QUEUE.value:
                    tmdb_id         = str(item.get("ProviderIds", {}).get("Tmdb") or "")
                    media_type_item = item.get("Type") or "Movie"
                    seerr_data      = (seerr_cache or {}).get(tmdb_id) if tmdb_id else None
                    seerr_id_val    = seerr_data.get("seerr_id") if seerr_data else None
                    seerr_username_val = seerr_data.get("username", "") if seerr_data else ""

                    radarr_id_val: Optional[int]  = None
                    sonarr_id_val: Optional[int]  = None
                    sonarr_series_id_val: Optional[int] = None
                    season_number_val: Optional[int]    = None

                    if media_type_item == "Movie":
                        radarr_id_val = radarr_find_by_path_cached(file_path, radarr_cache) if radarr_cache is not None else None
                    else:
                        sonarr_entry = sonarr_get_cache_entry(file_path, sonarr_cache) if sonarr_cache is not None else None
                        if sonarr_entry:
                            sonarr_id_val        = sonarr_entry["ef_id"]
                            sonarr_series_id_val = sonarr_entry["series_id"]
                            season_number_val     = sonarr_entry["season_number"]

                    poster_url_val = await _get_poster_url(
                        emby_id, tmdb_id=tmdb_id, media_type=media_type_item,
                        radarr_id=radarr_id_val, sonarr_id=sonarr_id_val,
                    )
                    seerr_request_url = ""
                    if seerr_id_val and seerr_ext_url:
                        path = "movie" if media_type_item == "Movie" else "tv"
                        seerr_request_url = f"{seerr_ext_url.rstrip('/')}/{path}/{tmdb_id}"

                    detect_at = now_utc()
                    delete_at = detect_at + timedelta(days=lib.get("grace_days") or 7)
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
                    eligible.append(expert_entry)
                    await add_log("INFO", f"Expert rule match (queue) : {item.get('Name') or emby_id}", "scan")

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


async def reevaluate_library_queue(library_id: str) -> int:
    """Recheck conditions for pending items in a library. Remove those no longer matching."""
    async with get_db() as db:
        lib = await db.fetch_one("SELECT * FROM libraries WHERE id=?", (library_id,))
        if not lib:
            return 0
        pending = await db.fetch_all(
            "SELECT * FROM media_queue WHERE library_id=? AND status='pending'",
            (library_id,),
        )

    if not pending:
        return 0

    conditions = json.loads(lib.get("conditions") or "[]")
    logic      = lib.get("logic") or "AND"
    server_id  = str(lib.get("server_id") or "0")
    users      = await get_users(server_id=server_id)
    user_ids   = [u["Id"] for u in users] if users else []
    removed    = 0

    for row in pending:
        emby_id      = row["emby_id"]
        added_date   = parse_iso_dt(row.get("added_date"))
        last_played  = None
        play_count   = 0
        never_watched = True
        raw_lp = parse_iso_dt(row.get("last_played"))
        if raw_lp:
            last_played   = raw_lp
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

        if not _evaluate_conditions(conditions, logic, added_date, last_played, play_count, never_watched):
            poster_url = row.get("poster_url", "")
            if poster_url and poster_url.startswith("http") and emby_id:
                try:
                    _lib_srv = str(lib.get("server_id") or "0")
                    emby_url_val, emby_key_val = await get_client(_lib_srv)
                    overlay_on = await get_bool_setting("emby_leaving_soon_overlay")
                    if overlay_on:
                        async with httpx.AsyncClient(timeout=10) as hc:
                            pr = await hc.get(poster_url, follow_redirects=True)
                            if pr.status_code == 200 and pr.headers.get("content-type", "").startswith("image"):
                                b64 = base64.b64encode(pr.content).decode("ascii")
                                await hc.post(
                                    f"{emby_url_val}/Items/{emby_id}/Images/Primary",
                                    headers={"X-Emby-Token": emby_key_val, "Content-Type": "image/jpeg"},
                                    content=b64,
                                )
                except Exception as e_rp:
                    logger.debug(f"Restore poster (reevaluate): {e_rp}")

            async with get_db() as db:
                await db.execute("DELETE FROM media_queue WHERE id=?", (row["id"],))
                await db.commit()
            removed += 1
            await add_log("INFO", f"Retiré (conditions changées) : {row['title']}", "scan")

    if removed:
        await add_log("INFO", f"Réévaluation {lib['name']} : {removed} média(s) retirés", "scan")
        await sync_emby_collection()
    return removed
```

- [ ] **Step 2: Verify importable**

```bash
cd /opt/claude/hygie && python -c "from backend.scanner._emby_scanner import _scan_library, reevaluate_library_queue; print('OK')"
```

- [ ] **Step 3: Run existing tests**

```bash
cd /opt/claude/hygie && python -m pytest backend/tests/ -v 2>&1 | tail -10
```
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git -C /opt/claude/hygie add backend/scanner/_emby_scanner.py
git -C /opt/claude/hygie commit -m "refactor(scanner): extract _emby_scanner.py"
```

---

### Task 6: Extract _orchestrator.py

**Files:**
- Create: `backend/scanner/_orchestrator.py`

Lines 168–345 of scanner.py (`run_scan`, `run_scan_library`). These are the public entry points — they acquire the lock, call sub-scanners, and handle job logging.

- [ ] **Step 1: Create the module**

```python
# backend/scanner/_orchestrator.py
"""Top-level scan orchestrator — acquires the scan lock and dispatches per-library work."""
import asyncio
import logging
from typing import Optional

from .._job_state import _scan_lock
from ..db.engine import get_db
from ..db.settings_store import get_setting, get_bool_setting, get_int_setting
from ..db.media_servers import get_media_servers
from ..db.logs import add_job_run, add_log, finish_job_run
from ..db.repositories import get_enabled_libraries, get_queued_and_ignored_ids
from ..emby_client import get_users
from ..arr_clients import (
    build_radarr_path_cache,
    build_seerr_request_cache,
    build_sonarr_path_cache,
)
from ..exceptions import ArrClientError
from ..discord_client import send_alert
from ..notifications import _ensure_notif_columns, _send_pending_notifications
from ..collection import sync_emby_collection
from ._emby_scanner import _scan_library
from ._plex_scanner import _scan_plex_library

logger = logging.getLogger(__name__)


async def run_scan() -> None:
    """Full scan of all enabled libraries across all enabled servers."""
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
            servers = await get_media_servers()
            enabled_servers = [s for s in servers if s.get("enabled", True)]
            if not enabled_servers:
                enabled_servers = [{"id": "0", "type": await get_setting("media_server_type") or "emby"}]

            for server in enabled_servers:
                server_id   = str(server.get("id", "0"))
                server_type = server.get("type", "")

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
                    await add_log("INFO", f"Serveur {server_id} ignoré (type: {server_type})", "scan")
                    continue

                libraries = await get_enabled_libraries(server_id)
                if not libraries:
                    continue

                users    = await get_users(server_id=server_id)
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
                        _msg     = await get_setting("discord_alert_seerr_failure_msg") or ""
                        await send_alert(
                            "🔌 Seerr inaccessible", str(_seerr_err), "warning",
                            mention=_mention, custom_msg=_msg,
                            template_vars={"detail": str(_seerr_err)},
                        )

                queued_ids, ignored_ids = await get_queued_and_ignored_ids()

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
        await add_log("WARN", "Scan déjà en cours — ignoré", "job")
        return

    async with _scan_lock:
        run_id = await add_job_run("scan_library")
        await add_log("INFO", f"Scan bibliothèque : {library_id}", "job")
        _sl_status, _sl_msg = "error", ""
        try:
            async with get_db() as db:
                lib = await db.fetch_one(
                    "SELECT * FROM libraries WHERE id=? AND enabled=1",
                    (library_id,),
                )

            if not lib:
                await add_log("WARN", f"Bibliothèque {library_id} introuvable", "scan")
                _sl_status, _sl_msg = "warning", "Library not found"
                return

            server_id = str(lib.get("server_id") or "0")
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
```

- [ ] **Step 2: Verify importable**

```bash
cd /opt/claude/hygie && python -c "from backend.scanner._orchestrator import run_scan, run_scan_library; print('OK')"
```

- [ ] **Step 3: Run existing tests**

```bash
cd /opt/claude/hygie && python -m pytest backend/tests/ -v 2>&1 | tail -10
```

- [ ] **Step 4: Commit**

```bash
git -C /opt/claude/hygie add backend/scanner/_orchestrator.py
git -C /opt/claude/hygie commit -m "refactor(scanner): extract _orchestrator.py (run_scan, run_scan_library)"
```

---

### Task 7: Wire __init__.py and delete scanner.py

**Files:**
- Modify: `backend/scanner/__init__.py`
- Delete: `backend/scanner.py`

Final step: the public API is re-exported from `__init__.py` so all existing `from .scanner import run_scan` imports keep working. Then `scanner.py` is deleted.

- [ ] **Step 1: Fill in `__init__.py`**

```python
# backend/scanner/__init__.py
"""scanner package — public API.

All callers use: from .scanner import run_scan, run_scan_library, reevaluate_library_queue
This package replaces the old scanner.py monolith.
"""
from ._orchestrator import run_scan, run_scan_library
from ._emby_scanner import reevaluate_library_queue

__all__ = ["run_scan", "run_scan_library", "reevaluate_library_queue"]
```

- [ ] **Step 2: Verify the public API is importable via the package**

```bash
cd /opt/claude/hygie && python -c "
from backend.scanner import run_scan, run_scan_library, reevaluate_library_queue
print('OK — public API accessible')
"
```
Expected: `OK — public API accessible`

- [ ] **Step 3: Verify scheduler.py still works (it imports from .scanner)**

```bash
cd /opt/claude/hygie && python -c "
from backend.scheduler import run_scan, run_scan_library, reevaluate_library_queue
print('OK — scheduler re-export works')
"
```
Expected: `OK — scheduler re-export works`

- [ ] **Step 4: Delete the old monolith**

```bash
rm /opt/claude/hygie/backend/scanner.py
```

- [ ] **Step 5: Final import check — confirm nothing breaks**

```bash
cd /opt/claude/hygie && python -c "
import backend.main
print('OK — main app imports cleanly')
"
```
Expected: `OK — main app imports cleanly`

- [ ] **Step 6: Run all tests**

```bash
cd /opt/claude/hygie && python -m pytest backend/tests/ -v 2>&1
```
Expected: all tests pass, no import errors.

- [ ] **Step 7: Commit**

```bash
git -C /opt/claude/hygie add backend/scanner/__init__.py
git -C /opt/claude/hygie rm backend/scanner.py
git -C /opt/claude/hygie commit -m "refactor(scanner): wire scanner/__init__.py, delete scanner.py monolith"
```

---

## Self-Review

**1. Spec coverage:**
- ✅ scanner.py (794 lines) → 6 focused modules (avg ~130 lines each)
- ✅ Circular import eliminated: `from .plex_client import build_plex_client` is now top-level in `_plex_scanner.py`
- ✅ Public API (`run_scan`, `run_scan_library`, `reevaluate_library_queue`) unchanged — all callers work without modification
- ✅ `scheduler.py` continues to re-export the public API transparently

**2. Placeholder scan:**
- No TBD/TODO — all module code is complete and verbatim from scanner.py

**3. Type consistency:**
- `_build_queue_entry` signature unchanged (same kwargs)
- `_insert_queue_entry(entry, queued_ids, dry_run)` — matches usage in `_emby_scanner.py` and `_consolidation.py`
- `_evaluate_expert_rules(item_data, library_id)` → `Optional[str]` — matches usage in `_emby_scanner.py`

**Deferred items (not in this plan):**
- `emby_id` column rename to `media_id` — schema migration required, tracked separately
- Application services layer — separate plan
- Integration tests for full scan path — separate plan
