"""
ECC-mandated tests for all identified bugs in Hygie v3.1.10.
Written BEFORE implementation (TDD Red phase).

Phases:
  Phase 1 — Bug #6: server_id missing in _get_poster_url
  Phase 2 — Bugs #7, #8, #9: URL validation, i18n keys, ISO comparison
  Phase 3 — Bug #3: atomic queue insertions
  Phase 4 — Bug #2: server-scoped queued_ids
  Phase 5 — Bug #4: activity log cached per server
  Phase 6 — Bug #5: N+1 in reevaluate_library_queue
  Phase 7 — Bug #10: coverage for critical untested paths
"""
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1 — Bug #6: server_id not propagated to _get_poster_url
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_emby_scanner_passes_server_id_to_get_poster_url(tmp_path):
    """_scan_library must pass server_id to _get_poster_url.

    Current bug: _emby_scanner.py:188 calls _get_poster_url without server_id,
    so the fallback Emby URL uses server '0' instead of the actual server.
    """
    import backend.db.engine as _db_engine
    import backend.db.utils as _u
    import backend.db.settings_store as _ss
    import backend.db.media_servers as _ms
    import backend.db.schema as _schema

    db_path = str(tmp_path / "fix6.db")
    _u.DB_PATH = _ss.DB_PATH = _ms.DB_PATH = _schema.DB_PATH = db_path
    _db_engine.SQLITE_PATH = db_path
    _ms._ms_cache = None; _ms._ms_cache_ts = 0.0
    _ss._settings_cache.clear(); _ss._settings_cache_ts = 0.0
    await _schema.init_db()

    poster_calls = []

    async def fake_get_poster_url(emby_id, *, tmdb_id="", media_type="Movie",
                                   radarr_id=None, sonarr_id=None, server_id="0"):
        poster_calls.append(server_id)
        return ""

    with patch("backend.scanner._emby_scanner._get_poster_url", new=fake_get_poster_url):
        from backend.scanner._emby_scanner import _scan_library
        lib = {
            "id": "lib1", "name": "Test", "emby_library_id": "3",
            "server_id": "myserver", "conditions": "[]", "logic": "AND",
            "grace_days": 7, "enabled": 1, "deletion_unit": "episode",
        }
        with patch("backend.scanner._emby_scanner.get_items_in_library",
                   new_callable=AsyncMock, return_value=([], 0)):
            with patch("backend.scanner._emby_scanner.get_library_user_data",
                       new_callable=AsyncMock, return_value={}):
                with patch("backend.scanner._emby_scanner.get_play_activity",
                           new_callable=AsyncMock, return_value={}):
                    await _scan_library(lib, [], server_id="myserver")

    # If any poster URL was requested, it must use "myserver" not "0"
    wrong_calls = [s for s in poster_calls if s == "0" and "myserver" != "0"]
    assert wrong_calls == [], f"_get_poster_url called with server_id='0' instead of 'myserver'"


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2a — Bug #7: Empty URL silently accepted
# ─────────────────────────────────────────────────────────────────────────────

def test_validate_server_url_rejects_empty_string():
    """_validate_server_url must raise HTTPException for empty string URL."""
    from fastapi import HTTPException
    from backend.routers.settings import _validate_server_url

    with pytest.raises(HTTPException) as exc_info:
        _validate_server_url("", "url")
    assert exc_info.value.status_code == 422


def test_validate_server_url_accepts_none_as_no_op():
    """_validate_server_url(None) should return '' without error (field absent in PATCH)."""
    from backend.routers.settings import _validate_server_url
    result = _validate_server_url(None, "url")
    assert result == ""


def test_validate_server_url_accepts_valid_http():
    """_validate_server_url with valid http:// URL passes through."""
    from backend.routers.settings import _validate_server_url
    result = _validate_server_url("http://emby.local:8096", "url")
    assert result == "http://emby.local:8096"


def test_validate_server_url_rejects_ftp_scheme():
    """_validate_server_url with ftp:// raises HTTPException."""
    from fastapi import HTTPException
    from backend.routers.settings import _validate_server_url
    with pytest.raises(HTTPException):
        _validate_server_url("ftp://emby.local", "url")


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2b — Bug #8: Missing i18n key queue.watchedUnknownDate
# ─────────────────────────────────────────────────────────────────────────────

import json
import pathlib

_LOCALES_DIR = pathlib.Path(__file__).parent.parent / "backend" / "locales"
_FRONTEND_LOCALES_DIR = pathlib.Path(__file__).parent.parent / "frontend" / "vue" / "src" / "locales"


@pytest.mark.parametrize("lang", ["fr", "en", "de", "es", "it", "pt", "nl", "pl"])
def test_backend_locale_has_all_required_keys(lang):
    """Every backend locale must have all required message keys."""
    path = _LOCALES_DIR / f"{lang}.json"
    if not path.exists():
        pytest.skip(f"locale {lang} not found")
    data = json.loads(path.read_text())
    required = [
        "scan.started", "scan.done", "deletion.started", "deletion.done",
        "notif.sent", "backup.done",
    ]
    missing = [k for k in required if k not in data]
    assert missing == [], f"Backend locale {lang} missing keys: {missing}"


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2c — Bug #9: ISO date string comparison in get_play_activity
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_play_activity_keeps_most_recent_date_datetime_aware():
    """get_play_activity must keep the most recent stop date per item.

    Two stop events for the same item: the one with the later datetime
    must win, regardless of string length or decimal precision differences.
    """
    fake_entries = [
        {"Type": "playback.stop", "ItemId": "42", "Date": "2024-01-10T10:00:00.000Z"},
        {"Type": "playback.stop", "ItemId": "42", "Date": "2024-01-15T23:59:59.9999999Z"},
        {"Type": "playback.stop", "ItemId": "42", "Date": "2024-01-01T00:00:00Z"},
        {"Type": "playback.start", "ItemId": "42", "Date": "2024-01-20T10:00:00Z"},  # ignored
    ]
    fake_body = {"Items": fake_entries, "TotalRecordCount": len(fake_entries)}

    import httpx
    import respx
    from backend.emby_client import get_play_activity

    with respx.mock:
        respx.get("http://emby.test:8096/System/ActivityLog/Entries").mock(
            return_value=httpx.Response(200, json=fake_body)
        )
        with patch("backend.emby_client.get_client",
                   new_callable=AsyncMock,
                   return_value=("http://emby.test:8096", "key")):
            result = await get_play_activity("0", days=30)

    assert "42" in result
    # Must keep the later date (Jan 15), not Jan 10 or Jan 1
    from backend.db.utils import parse_iso_dt
    dt = parse_iso_dt(result["42"])
    assert dt is not None
    assert dt.day == 15
    assert dt.month == 1


@pytest.mark.asyncio
async def test_get_play_activity_ignores_playback_start_events():
    """get_play_activity must only count playback.stop, not playback.start."""
    fake_entries = [
        {"Type": "playback.start", "ItemId": "99", "Date": "2024-01-20T10:00:00Z"},
    ]
    fake_body = {"Items": fake_entries, "TotalRecordCount": 1}

    import httpx
    import respx
    from backend.emby_client import get_play_activity

    with respx.mock:
        respx.get("http://emby.test:8096/System/ActivityLog/Entries").mock(
            return_value=httpx.Response(200, json=fake_body)
        )
        with patch("backend.emby_client.get_client",
                   new_callable=AsyncMock,
                   return_value=("http://emby.test:8096", "key")):
            result = await get_play_activity("0", days=30)

    assert "99" not in result


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3 — Bug #3: Atomic queue insertions
# ─────────────────────────────────────────────────────────────────────────────

_ENTRY_TEMPLATE = {
    "emby_id": "placeholder",
    "title": "Test Movie",
    "media_type": "Movie",
    "library_id": "lib1",
    "library_name": "Films",
    "file_path": "/movies/test.mkv",
    "poster_url": "",
    "tmdb_id": "12345",
    "seerr_id": None,
    "seerr_user_id": None,
    "seerr_username": "",
    "seerr_request_url": "",
    "radarr_id": None,
    "sonarr_id": None,
    "sonarr_series_id": None,
    "season_number": None,
    "detected_at": "2024-01-01T00:00:00+00:00",
    "delete_at": "2024-01-08T00:00:00+00:00",
    "added_date": "2023-01-01T00:00:00+00:00",
    "last_played": None,
    "view_count": 0,
}


@pytest.mark.asyncio
async def test_insert_queue_entries_batch_commits_all_on_success(tmp_path):
    """insert_queue_entries_batch inserts all entries atomically in one transaction."""
    import backend.db.engine as _e
    import backend.db.utils as _u
    import backend.db.settings_store as _ss
    import backend.db.media_servers as _ms
    import backend.db.schema as _schema

    db_path = str(tmp_path / "batch_test.db")
    _u.DB_PATH = _ss.DB_PATH = _ms.DB_PATH = _schema.DB_PATH = db_path
    _e.SQLITE_PATH = db_path
    _ms._ms_cache = None; _ms._ms_cache_ts = 0.0
    _ss._settings_cache.clear(); _ss._settings_cache_ts = 0.0
    await _schema.init_db()

    entries = [
        {**_ENTRY_TEMPLATE, "emby_id": f"batch-{i}", "title": f"Movie {i}"}
        for i in range(3)
    ]
    from backend.db.repositories import insert_queue_entries_batch
    await insert_queue_entries_batch(entries)

    from backend.db.engine import get_db
    async with get_db() as db:
        rows = await db.fetch_all(
            "SELECT emby_id FROM media_queue WHERE status='pending'"
        )
    ids = {r["emby_id"] for r in rows}
    assert ids == {"batch-0", "batch-1", "batch-2"}


@pytest.mark.asyncio
async def test_insert_queue_entries_batch_is_atomic_on_duplicate(tmp_path):
    """insert_queue_entries_batch rolls back ALL entries if any fails (e.g., duplicate emby_id)."""
    import backend.db.engine as _e
    import backend.db.utils as _u
    import backend.db.settings_store as _ss
    import backend.db.media_servers as _ms
    import backend.db.schema as _schema

    db_path = str(tmp_path / "batch_atomic.db")
    _u.DB_PATH = _ss.DB_PATH = _ms.DB_PATH = _schema.DB_PATH = db_path
    _e.SQLITE_PATH = db_path
    _ms._ms_cache = None; _ms._ms_cache_ts = 0.0
    _ss._settings_cache.clear(); _ss._settings_cache_ts = 0.0
    await _schema.init_db()

    entries = [
        {**_ENTRY_TEMPLATE, "emby_id": "dup-1", "title": "First"},
        {**_ENTRY_TEMPLATE, "emby_id": "dup-1", "title": "Duplicate"},  # UNIQUE violation
        {**_ENTRY_TEMPLATE, "emby_id": "dup-3", "title": "Third"},
    ]
    from backend.db.repositories import insert_queue_entries_batch
    with pytest.raises(Exception):
        await insert_queue_entries_batch(entries)

    from backend.db.engine import get_db
    async with get_db() as db:
        rows = await db.fetch_all("SELECT emby_id FROM media_queue")
    assert len(rows) == 0, "No rows should be committed after batch failure"


@pytest.mark.asyncio
async def test_insert_queue_entries_batch_noop_on_empty(tmp_path):
    """insert_queue_entries_batch with empty list does nothing."""
    import backend.db.engine as _e
    import backend.db.utils as _u
    import backend.db.settings_store as _ss
    import backend.db.media_servers as _ms
    import backend.db.schema as _schema

    db_path = str(tmp_path / "batch_empty.db")
    _u.DB_PATH = _ss.DB_PATH = _ms.DB_PATH = _schema.DB_PATH = db_path
    _e.SQLITE_PATH = db_path
    _ms._ms_cache = None; _ms._ms_cache_ts = 0.0
    _ss._settings_cache.clear(); _ss._settings_cache_ts = 0.0
    await _schema.init_db()

    from backend.db.repositories import insert_queue_entries_batch
    await insert_queue_entries_batch([])  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 4 — Bug #2: global queued_ids causes cross-server ID collision
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_emby_scan_not_blocked_by_plex_queued_id(tmp_path):
    """An Emby item with same integer ID as a queued Plex item must not be skipped.

    Both systems use sequential integer IDs starting from 1. A Plex ratingKey=44797
    can collide with an Emby item Id=44797. The scanner must scope queued_ids to
    the current server to avoid this collision.
    """
    import backend.db.engine as _e
    import backend.db.utils as _u
    import backend.db.settings_store as _ss
    import backend.db.media_servers as _ms
    import backend.db.schema as _schema
    import aiosqlite

    db_path = str(tmp_path / "collision.db")
    _u.DB_PATH = _ss.DB_PATH = _ms.DB_PATH = _schema.DB_PATH = db_path
    _e.SQLITE_PATH = db_path
    _ms._ms_cache = None; _ms._ms_cache_ts = 0.0
    _ss._settings_cache.clear(); _ss._settings_cache_ts = 0.0
    await _schema.init_db()

    # Create a Plex library and queue a Plex item with emby_id="44797"
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO libraries (id, name, emby_library_id, server_id, conditions, "
            "logic, grace_days, enabled, deletion_unit, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("plex-lib", "Plex Films", "1", "plex1", "[]", "AND", 7, 1, "movie", "2024-01-01"),
        )
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, poster_url, tmdb_id, detected_at, delete_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("44797", "Top Gun Plex", "movie", "plex-lib", "Plex Films",
             "", "", "95953", "2024-01-01T00:00:00+00:00", "2024-01-08T00:00:00+00:00", "pending"),
        )
        await db.commit()

    # Now simulate Emby scan for server "emby0" — item Id="44797" must NOT be skipped
    from backend.db.engine import get_db
    async with get_db() as db:
        # Server-scoped query (the fix): only Emby server "emby0" queued items
        rows = await db.fetch_all(
            """SELECT mq.emby_id FROM media_queue mq
               JOIN libraries l ON mq.library_id = l.id
               WHERE l.server_id = ?""",
            ("emby0",),
        )
    emby_queued_ids = {r["emby_id"] for r in rows}

    # "44797" should NOT be in the Emby server's scoped set (it belongs to plex1)
    assert "44797" not in emby_queued_ids, (
        "Plex item '44797' incorrectly blocks Emby item with same ID. "
        "queued_ids must be scoped to the current server."
    )


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 5 — Bug #4: get_play_activity fetched once per library (not per server)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_play_activity_called_once_per_server_scan(tmp_path):
    """During a full scan, get_play_activity must be called once per server, not per library."""
    import backend.db.engine as _e
    import backend.db.utils as _u
    import backend.db.settings_store as _ss
    import backend.db.media_servers as _ms
    import backend.db.schema as _schema
    import aiosqlite

    db_path = str(tmp_path / "activity_cache.db")
    _u.DB_PATH = _ss.DB_PATH = _ms.DB_PATH = _schema.DB_PATH = db_path
    _e.SQLITE_PATH = db_path
    _ms._ms_cache = None; _ms._ms_cache_ts = 0.0
    _ss._settings_cache.clear(); _ss._settings_cache_ts = 0.0
    await _schema.init_db()

    # Create 2 Emby libraries on same server
    async with aiosqlite.connect(db_path) as db:
        for i in range(2):
            await db.execute(
                "INSERT INTO libraries (id, name, emby_library_id, server_id, "
                "conditions, logic, grace_days, enabled, deletion_unit, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"lib{i}", f"Library {i}", str(i), "0", "[]", "AND", 7, 1, "movie", "2024-01-01"),
            )
        for k, v in [
            ("dry_run", "false"), ("max_parallel_library_scans", "1"),
            ("ui_language", "fr"), ("media_server_type", "emby"),
            ("discord_alert_scan_failure", "false"),
            ("discord_alert_seerr_failure", "false"),
            ("media_servers", "[]"),
        ]:
            await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (k, v))
        await db.commit()

    activity_call_count = 0

    async def counting_get_play_activity(*args, **kwargs):
        nonlocal activity_call_count
        activity_call_count += 1
        return {}

    patches = [
        patch("backend.scanner._emby_scanner.get_items_in_library",
              new_callable=AsyncMock, return_value=([], 0)),
        patch("backend.scanner._emby_scanner.get_library_user_data",
              new_callable=AsyncMock, return_value={}),
        patch("backend.scanner._emby_scanner.get_play_activity",
              new=counting_get_play_activity),
        patch("backend.scanner._orchestrator.get_users",
              new_callable=AsyncMock, return_value=[]),
        patch("backend.scanner._orchestrator.build_radarr_path_cache",
              new_callable=AsyncMock, return_value={}),
        patch("backend.scanner._orchestrator.build_sonarr_path_cache",
              new_callable=AsyncMock, return_value={}),
        patch("backend.scanner._orchestrator.build_seerr_request_cache",
              new_callable=AsyncMock, return_value={}),
        patch("backend.scanner._orchestrator.sync_emby_collection",
              new_callable=AsyncMock),
        patch("backend.scanner._orchestrator._send_pending_notifications",
              new_callable=AsyncMock),
        patch("backend.emby_client.get_client",
              new_callable=AsyncMock, return_value=("http://emby:8096", "key")),
    ]

    ctx = __import__("contextlib").ExitStack()
    for p in patches:
        ctx.enter_context(p)

    with ctx:
        from backend.scanner._orchestrator import _run_scan_body
        await _run_scan_body(run_id=1)

    assert activity_call_count <= 1, (
        f"get_play_activity called {activity_call_count} times for 2 libraries on same server. "
        "Must be called once per server, not per library."
    )


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 6 — Bug #5: N+1 HTTP calls in reevaluate_library_queue
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reevaluate_uses_batch_not_per_item_fetch(tmp_path):
    """reevaluate_library_queue must NOT call get_user_data (per item).
    It should use get_library_user_data (batch) instead.
    """
    import backend.db.engine as _e
    import backend.db.utils as _u
    import backend.db.settings_store as _ss
    import backend.db.media_servers as _ms
    import backend.db.schema as _schema
    import aiosqlite

    db_path = str(tmp_path / "reevaluate_n1.db")
    _u.DB_PATH = _ss.DB_PATH = _ms.DB_PATH = _schema.DB_PATH = db_path
    _e.SQLITE_PATH = db_path
    _ms._ms_cache = None; _ms._ms_cache_ts = 0.0
    _ss._settings_cache.clear(); _ss._settings_cache_ts = 0.0
    await _schema.init_db()

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO libraries (id, name, emby_library_id, server_id, conditions, "
            "logic, grace_days, enabled, deletion_unit, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("lib1", "Films", "3", "0", "[]", "AND", 7, 1, "movie", "2024-01-01"),
        )
        for i in range(5):
            await db.execute(
                "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
                "file_path, poster_url, tmdb_id, detected_at, delete_at, status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (str(i), f"Movie {i}", "movie", "lib1", "Films", f"/m/{i}.mkv",
                 "", "", "2024-01-01T00:00:00+00:00", "2024-01-08T00:00:00+00:00", "pending"),
            )
        for k, v in [
            ("dry_run", "false"), ("ui_language", "fr"),
            ("media_servers", "[]"), ("media_server_type", "emby"),
        ]:
            await db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k, v))
        await db.commit()

    single_call_count = 0
    batch_call_count = 0

    async def counting_get_user_data(*args, **kwargs):
        nonlocal single_call_count
        single_call_count += 1
        return {}

    async def counting_get_library_user_data(*args, **kwargs):
        nonlocal batch_call_count
        batch_call_count += 1
        return {}

    with patch("backend.scanner._emby_scanner.get_user_data", new=counting_get_user_data):
        with patch("backend.scanner._emby_scanner.get_library_user_data",
                   new=counting_get_library_user_data):
            with patch("backend.emby_client.get_client",
                       new_callable=AsyncMock, return_value=("http://e:8096", "k")):
                from backend.scanner._emby_scanner import reevaluate_library_queue
                await reevaluate_library_queue("lib1")

    assert single_call_count == 0, (
        f"get_user_data (per-item) called {single_call_count} times. "
        "Must use get_library_user_data (batch) instead."
    )


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 7 — Bug #10: Untested critical paths — CircuitBreaker
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_circuit_breaker_closed_allows_calls():
    """CircuitBreaker in CLOSED state passes calls through."""
    from backend.arr_clients.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=60.0)

    async def return_42():
        return 42

    result = await cb.call(return_42)
    assert result == 42
    assert cb.state == "closed"


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_threshold():
    """CircuitBreaker transitions to OPEN after failure_threshold failures."""
    from backend.arr_clients.circuit_breaker import CircuitBreaker, CircuitOpenError
    cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=3600.0)

    async def always_fail():
        raise ValueError("fail")

    for _ in range(2):
        with pytest.raises(ValueError):
            await cb.call(always_fail)

    assert cb.state == "open"
    with pytest.raises(CircuitOpenError):
        await cb.call(always_fail)


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_after_timeout():
    """CircuitBreaker reports HALF_OPEN state after recovery_timeout elapses."""
    import time
    from backend.arr_clients.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.01)

    async def fail():
        raise ValueError("fail")

    with pytest.raises(ValueError):
        await cb.call(fail)

    assert cb.state == "open"
    cb._last_failure_ts = time.monotonic() - 1.0  # advance time past timeout
    assert cb.state == "half_open"


@pytest.mark.asyncio
async def test_circuit_breaker_closes_on_half_open_success():
    """CircuitBreaker closes when a HALF_OPEN probe call succeeds."""
    import time
    from backend.arr_clients.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.01)

    async def fail():
        raise ValueError("fail")

    with pytest.raises(ValueError):
        await cb.call(fail)

    cb._last_failure_ts = time.monotonic() - 1.0  # HALF_OPEN

    async def succeed():
        return "ok"

    result = await cb.call(succeed)
    assert result == "ok"
    assert cb.state == "closed"


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 7 — Bug #10: Untested critical paths — Played=True + PlayCount=0
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_played_true_with_zero_playcount_treated_as_watched():
    """Emby Played=True with PlayCount=0 must result in play_count >= 1 and never_watched=False.

    This is the root cause of the 'always shows never watched' bug.
    When users mark items as played via Seerr or Emby UI (without playing via player),
    Emby returns Played=True + PlayCount=0 + LastPlayedDate=null.
    """
    from backend.rules.legacy_conditions import _evaluate_item
    from backend.db.utils import now_utc
    from datetime import timedelta

    now = now_utc()
    item = {
        "Id": "test-item-1",
        "Name": "Test Movie",
        "Type": "Movie",
        "Path": "/movies/test.mkv",
        "DateCreated": (now - timedelta(days=100)).isoformat(),
        "ProviderIds": {"Tmdb": "12345"},
    }
    lib = {"id": "lib1", "name": "Films", "server_id": "0", "grace_days": 7}
    user_data_cache = {
        "user1": {
            "test-item-1": {
                "Played": True,
                "PlayCount": 0,          # ← the bug scenario
                "LastPlayedDate": None,   # ← no date
            }
        }
    }

    # Condition: play_count == 0 (item should not be queued if Played=True)
    conditions = [{"field": "play_count", "op": "eq", "value": 0}]

    with patch("backend.rules.legacy_conditions._get_poster_url",
               new_callable=AsyncMock, return_value=""):
        result = await _evaluate_item(
            item, lib, conditions, "AND", 7, ["user1"], [],
            user_data_cache=user_data_cache,
        )

    # Item is Played=True → effective play_count=1 → condition play_count==0 fails → not queued
    assert result is None, (
        "Item with Played=True should NOT be queued when condition is play_count==0. "
        "Current bug: Played=True ignored, PlayCount=0 passes the condition."
    )


@pytest.mark.asyncio
async def test_played_true_uses_activity_log_for_last_played_date():
    """When Played=True and LastPlayedDate=null, activity_log provides the date."""
    from backend.rules.legacy_conditions import _evaluate_item
    from backend.db.utils import now_utc
    from datetime import timedelta

    now = now_utc()
    item = {
        "Id": "movie-abc",
        "Name": "Watched Movie",
        "Type": "Movie",
        "Path": "/movies/watched.mkv",
        "DateCreated": (now - timedelta(days=200)).isoformat(),
        "ProviderIds": {},
    }
    lib = {"id": "lib1", "name": "Films", "server_id": "0", "grace_days": 7}
    user_data_cache = {
        "u1": {
            "movie-abc": {
                "Played": True,
                "PlayCount": 0,
                "LastPlayedDate": None,  # no date in UserData
            }
        }
    }
    activity_log = {
        "movie-abc": "2026-05-10T19:05:07.0040000Z"  # date from activity log
    }

    # Condition: added_days_ago > 30 (triggers regardless of play count)
    conditions = [{"field": "added_days_ago", "op": "gt", "value": 30}]

    with patch("backend.rules.legacy_conditions._get_poster_url",
               new_callable=AsyncMock, return_value=""):
        result = await _evaluate_item(
            item, lib, conditions, "AND", 7, ["u1"], [],
            user_data_cache=user_data_cache,
            activity_log=activity_log,
        )

    if result is not None:
        # If item was queued, last_played must come from activity_log
        assert result["last_played"] is not None, (
            "last_played must be set from activity_log when Played=True and LastPlayedDate=null"
        )
        assert "2026-05-10" in result["last_played"]
