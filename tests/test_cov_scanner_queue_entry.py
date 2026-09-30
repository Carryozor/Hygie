"""Coverage tests for backend/scanner/_queue_entry.py.

_pre_mark_applicable_thresholds prevents a duplicate "Xd warning" Discord
notification right after the "detected" one, when an item's grace period is
short enough that it already qualifies for a threshold at insertion time.
_insert_queue_entry is the actual DB write that puts a media item in the
deletion queue. Both are only exercised indirectly today (through
test_scan_integration.py / test_e2e_scan_queue_delete.py); this file targets
their error-handling and edge branches directly.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def isolated_db(monkeypatch, tmp_path):
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _db_ss
    import backend.db.media_servers as _db_ms
    import backend.db.schema as _db_schema
    import backend.db.engine as _db_engine

    db_path = str(tmp_path / "queue_entry_cov.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ms, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()
    yield db_path


def _now():
    return datetime.now(timezone.utc)


async def _insert_queue_row(emby_id: str, delete_at: datetime) -> int:
    from backend.db.engine import get_db
    async with get_db() as db:
        new_id = await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (emby_id, "Some Movie", "Movie", "lib1", "Films", "/m/x.mkv",
             _now().isoformat(), delete_at.isoformat(), "pending"),
        )
        await db.commit()
        return new_id


# ─── _build_queue_entry ──────────────────────────────────────────────────────────

def test_build_queue_entry_maps_emby_item_and_enrichment_fields():
    from backend.scanner._queue_entry import _build_queue_entry
    now = _now()
    added = now - timedelta(days=10)
    last_played = now - timedelta(days=2)
    item = {"Id": "e1", "Name": "Movie X", "Type": "Movie", "Path": "/m/x.mkv"}
    lib = {"id": "lib1", "name": "Films"}
    entry = _build_queue_entry(
        item, lib,
        detect_at=now, delete_at=now + timedelta(days=7),
        added_date=added, last_played=last_played,
        poster_url="http://poster", tmdb_id="123",
        seerr_id=5, seerr_user_id=9, seerr_username="bob",
        radarr_id=42, view_count=3,
    )
    assert entry["emby_id"] == "e1"
    assert entry["title"] == "Movie X"
    assert entry["library_id"] == "lib1"
    assert entry["added_date"] == added.isoformat()
    assert entry["last_played"] == last_played.isoformat()
    assert entry["view_count"] == 3
    assert entry["radarr_id"] == 42


def test_build_queue_entry_defaults_missing_name_path_and_added_date():
    """An item with no Name/Path/added_date must fall back safely (never
    crash, never silently produce a None title that breaks the UI)."""
    from backend.scanner._queue_entry import _build_queue_entry
    now = _now()
    item = {"Id": "e2"}  # no Name, no Path, no Type
    lib = {"id": "lib1", "name": "Films"}
    entry = _build_queue_entry(
        item, lib, detect_at=now, delete_at=now + timedelta(days=7),
        added_date=None, last_played=None,
    )
    assert entry["title"] == "?"
    assert entry["media_type"] == "Movie"
    assert entry["file_path"] == ""
    assert entry["added_date"] == now.isoformat()  # falls back to detect_at
    assert entry["last_played"] is None


# ─── _pre_mark_applicable_thresholds ────────────────────────────────────────────

async def test_pre_mark_thresholds_noop_when_row_missing():
    from backend.scanner._queue_entry import _pre_mark_applicable_thresholds
    # No media_queue row for this emby_id at all.
    await _pre_mark_applicable_thresholds("missing-id", (_now() + timedelta(days=7)).isoformat())
    from backend.db.engine import get_db
    async with get_db() as db:
        rows = await db.fetch_all("SELECT * FROM notifications")
    assert rows == []


async def test_pre_mark_thresholds_inserts_for_already_qualifying_threshold():
    """Grace period of 1 day with a configured 7d threshold -> the 7d
    notification must be pre-marked as already sent, so the periodic
    notifier doesn't fire it redundantly right after 'detected'."""
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('discord_notif_thresholds', '7,1')"
        )
        await db.commit()

    delete_at = _now() + timedelta(days=1)  # short grace, qualifies for both 7d and 1d thresholds
    row_id = await _insert_queue_row("e1", delete_at)

    from backend.scanner._queue_entry import _pre_mark_applicable_thresholds
    await _pre_mark_applicable_thresholds("e1", delete_at.isoformat())

    async with get_db() as db:
        rows = await db.fetch_all("SELECT threshold FROM notifications WHERE media_id=?", (row_id,))
    thresholds = {r["threshold"] for r in rows}
    assert thresholds == {"7d", "1d"}


async def test_pre_mark_thresholds_skips_threshold_not_yet_reached():
    """A long grace period (30 days) must NOT pre-mark the 7d threshold —
    that notification is still expected to fire later."""
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('discord_notif_thresholds', '7,1')"
        )
        await db.commit()

    delete_at = _now() + timedelta(days=30)
    row_id = await _insert_queue_row("e1", delete_at)

    from backend.scanner._queue_entry import _pre_mark_applicable_thresholds
    await _pre_mark_applicable_thresholds("e1", delete_at.isoformat())

    async with get_db() as db:
        rows = await db.fetch_all("SELECT threshold FROM notifications WHERE media_id=?", (row_id,))
    assert rows == []


async def test_pre_mark_thresholds_handles_naive_datetime_string():
    """delete_at strings without a timezone (naive) must be treated as UTC,
    not crash or silently mis-schedule the pre-mark check."""
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('discord_notif_thresholds', '1')"
        )
        await db.commit()

    delete_at = _now() + timedelta(hours=1)
    row_id = await _insert_queue_row("e1", delete_at)
    naive_iso = delete_at.replace(tzinfo=None).isoformat()  # no +00:00 suffix

    from backend.scanner._queue_entry import _pre_mark_applicable_thresholds
    await _pre_mark_applicable_thresholds("e1", naive_iso)

    async with get_db() as db:
        rows = await db.fetch_all("SELECT threshold FROM notifications WHERE media_id=?", (row_id,))
    assert {r["threshold"] for r in rows} == {"1d"}


async def test_pre_mark_thresholds_swallows_non_string_threshold_setting():
    """If discord_notif_thresholds resolves to something that isn't a string
    (defensive: get_setting is expected to return str|None, but a corrupt DB
    value could break that contract), it must not propagate and abort the queue
    insert that's about to follow it — resolve_thresholds falls back to 7,1."""
    await _insert_queue_row("e1", _now() + timedelta(days=1))
    from backend.scanner._queue_entry import _pre_mark_applicable_thresholds
    with patch(
        "backend.db.settings_store.get_setting",
        new=AsyncMock(return_value=12345),  # not a string -> .split() raises
    ):
        await _pre_mark_applicable_thresholds("e1", (_now() + timedelta(days=1)).isoformat())  # must not raise


async def test_pre_mark_thresholds_swallows_unparseable_delete_at():
    """A corrupt delete_at string must not crash the queue insert path."""
    await _insert_queue_row("e1", _now() + timedelta(days=1))
    from backend.scanner._queue_entry import _pre_mark_applicable_thresholds
    await _pre_mark_applicable_thresholds("e1", "not-a-real-date")  # must not raise


async def test_pre_mark_thresholds_falls_back_to_default_on_unparseable_setting():
    """discord_notif_thresholds containing garbage (no valid token) must not
    crash NOR silently disable the alerts: it falls back to the default 7,1, so
    an item due in 1 day is pre-marked for both thresholds."""
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('discord_notif_thresholds', 'garbage,,x')"
        )
        await db.commit()
    row_id = await _insert_queue_row("e1", _now() + timedelta(days=1))

    from backend.scanner._queue_entry import _pre_mark_applicable_thresholds
    await _pre_mark_applicable_thresholds("e1", (_now() + timedelta(days=1)).isoformat())

    from backend.db.engine import get_db as _get_db
    async with _get_db() as db:
        rows = await db.fetch_all("SELECT threshold FROM notifications WHERE media_id=?", (row_id,))
    assert {r["threshold"] for r in rows} == {"7d", "1d"}


# ─── _insert_queue_entry ────────────────────────────────────────────────────────

def _entry(emby_id="e1", **overrides) -> dict:
    now = _now()
    base = {
        "emby_id": emby_id, "title": "Movie X", "media_type": "Movie",
        "library_id": "lib1", "library_name": "Films", "file_path": "/m/x.mkv",
        "poster_url": "", "tmdb_id": "", "seerr_id": None, "seerr_user_id": None,
        "seerr_username": "", "seerr_request_url": "", "radarr_id": None,
        "sonarr_id": None, "sonarr_series_id": None, "season_number": None,
        "arr_server_url": None, "detected_at": now.isoformat(),
        "delete_at": (now + timedelta(days=7)).isoformat(),
        "added_date": now.isoformat(), "last_played": None, "view_count": 0,
    }
    base.update(overrides)
    return base


async def test_insert_queue_entry_adds_to_queued_ids_set():
    queued_ids: set = set()
    with (
        patch("backend.scanner._queue_entry.send_notification", new=AsyncMock(return_value=True)),
        patch("backend.scanner._queue_entry.mark_notified_detected", new=AsyncMock()),
    ):
        from backend.scanner._queue_entry import _insert_queue_entry
        await _insert_queue_entry(_entry(), queued_ids, dry_run=False)
    assert "e1" in queued_ids


async def test_insert_queue_entry_marks_notified_only_when_notification_sent():
    with (
        patch("backend.scanner._queue_entry.send_notification", new=AsyncMock(return_value=False)),
        patch("backend.scanner._queue_entry.mark_notified_detected", new=AsyncMock()) as mock_mark,
    ):
        from backend.scanner._queue_entry import _insert_queue_entry
        await _insert_queue_entry(_entry(emby_id="e2"), set(), dry_run=False)
    mock_mark.assert_not_awaited()


async def test_insert_queue_entry_survives_notification_exception():
    """A Discord webhook failure must not roll back or block the queue
    insert — the item is already safely in the DB by the time we notify."""
    await_calls = []
    with (
        patch(
            "backend.scanner._queue_entry.send_notification",
            new=AsyncMock(side_effect=RuntimeError("discord down")),
        ),
        patch("backend.scanner._queue_entry.mark_notified_detected", new=AsyncMock()) as mock_mark,
    ):
        from backend.scanner._queue_entry import _insert_queue_entry
        await _insert_queue_entry(_entry(emby_id="e3"), set(), dry_run=False)  # must not raise
    mock_mark.assert_not_awaited()

    from backend.db.engine import get_db
    async with get_db() as db:
        row = await db.fetch_one("SELECT * FROM media_queue WHERE emby_id='e3'")
    assert row is not None  # the queue write itself must have succeeded
