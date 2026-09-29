"""Coverage for run_deletion()/_run_deletion_guarded() edges not exercised by
test_deletion_integration.py or test_watch_state_safety.py:

  * already-running skip
  * malformed discord_alert_error_threshold setting
  * per-item claim race (item claimed elsewhere)
  * per-item Discord failure alert
  * an exception escaping _delete_one() surfaced instead of silently dropped
  * the batch failure-threshold Discord alert
  * hard timeout and generic-exception job outcomes
  * LockNotAvailable handled by the guarded wrapper (multi-worker skip)
  * reset_stale_deleting() crash recovery
"""
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
import backend.db.schema as _db_schema
import backend.deletion as _deletion_mod

from backend.db.schema import init_db
from backend.db.utils import STATUS_PENDING


@pytest.fixture
async def deletion_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "run_deletion_edges.db")

    import backend.db.engine as _db_engine
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_deletion_mod, "DB_PATH", db_path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)

    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await init_db()

    settings = {
        "dry_run": "false",
        "discord_alert_deletion_error": "false",
        "discord_alert_error_threshold": "3",
        "qbit_action": "tag_only",
        "qbit_tag": "Supprimé",
    }
    async with aiosqlite.connect(db_path) as db:
        for k, v in settings.items():
            await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))
        await db.commit()

    yield db_path


_LIB_ID = "lib-edges-001"


async def _seed_library(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT OR IGNORE INTO libraries
               (id, name, emby_library_id, conditions, logic, grace_days,
                seerr_conditions, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_LIB_ID, "Test Library", "emby-lib-001", "[]", "AND", 0, "[]", 1,
             datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def _seed_item(db_path: str, emby_id: str, *, title: str = "The Batman") -> None:
    await _seed_library(db_path)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO media_queue
               (emby_id, title, media_type, library_id, library_name, file_path,
                poster_url, detected_at, delete_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (emby_id, title, "Movie", _LIB_ID, "Test Library", "/movies/x.mkv",
             "", now, past, STATUS_PENDING),
        )
        await db.commit()


def _base_patches() -> dict:
    """Fresh AsyncMocks per test — never share mock instances across tests,
    their call history would leak between assertions."""
    return dict(
        _find_torrent_hash=AsyncMock(return_value=None),
        _delete_from_seerr=AsyncMock(),
        sync_emby_collection=AsyncMock(),
        _send_pending_notifications=AsyncMock(),
        send_notification=AsyncMock(),
        get_client=AsyncMock(return_value=("http://emby:8096", "apikey")),
    )


# ─── Already running ────────────────────────────────────────────────────────

async def test_run_deletion_skips_entirely_when_already_locked(deletion_db, caplog):
    """A second concurrent run must log + return without touching the queue
    or job_history — the running instance owns this cycle."""
    from backend.deletion import run_deletion, _deletion_lock

    await _seed_item(deletion_db, "EMB-LOCK-001")

    monkey_locked = True

    with patch.object(_deletion_lock, "locked", lambda: monkey_locked):
        with caplog.at_level(logging.WARNING, logger="backend.deletion"):
            await run_deletion()

    async with aiosqlite.connect(deletion_db) as db:
        async with db.execute("SELECT COUNT(*) FROM job_history") as cur:
            (count,) = await cur.fetchone()
    assert count == 0, "a skipped run must not create a job_history row"


# ─── Malformed threshold setting ───────────────────────────────────────────

async def test_run_deletion_falls_back_to_default_threshold_on_malformed_setting(deletion_db):
    """A non-numeric discord_alert_error_threshold must not crash the job —
    it silently falls back to the default of 3."""
    from backend.deletion import run_deletion

    await _seed_item(deletion_db, "EMB-THR-001")
    async with aiosqlite.connect(deletion_db) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('discord_alert_error_threshold', 'not-a-number')"
        )
        await db.commit()

    with (
        patch("backend.deletion.delete_item", new_callable=AsyncMock, return_value=True),
        patch("backend.emby_client.delete_item", new_callable=AsyncMock, return_value=True),
        patch("backend.deletion._delete_from_arr", new_callable=AsyncMock, return_value=True),
        patch("backend.deletion.send_alert", new_callable=AsyncMock),
        patch.multiple("backend.deletion", **_base_patches()),
    ):
        await run_deletion()

    async with aiosqlite.connect(deletion_db) as db:
        async with db.execute("SELECT status, message FROM job_history") as cur:
            row = await cur.fetchone()
    assert row[0] == "success", f"malformed threshold must not fail the job: {row}"


# ─── Per-item claim race ───────────────────────────────────────────────────

async def test_run_deletion_skips_item_claimed_by_another_worker(deletion_db):
    """If _claim_pending() reports the item is no longer pending (claimed by
    the delete-now endpoint or another worker), it must be left completely
    alone — no status flip, no deletion attempt."""
    from backend.deletion import run_deletion

    await _seed_item(deletion_db, "EMB-RACE-001")

    with (
        patch("backend.deletion._claim_pending", new=AsyncMock(return_value=False)),
        patch("backend.deletion._delete_media", new=AsyncMock()) as mock_delete_media,
        patch("backend.deletion.send_alert", new_callable=AsyncMock),
        patch.multiple("backend.deletion", **_base_patches()),
    ):
        await run_deletion()

    mock_delete_media.assert_not_awaited()
    async with aiosqlite.connect(deletion_db) as db:
        async with db.execute(
            "SELECT status FROM media_queue WHERE emby_id='EMB-RACE-001'"
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == "pending", "an item claimed elsewhere must be left untouched"


# ─── Per-item Discord failure alert ────────────────────────────────────────

async def test_run_deletion_sends_per_item_alert_on_failure_when_enabled(deletion_db):
    """discord_alert_deletion_error=true must fire one alert per failed item."""
    from backend.deletion import run_deletion

    await _seed_item(deletion_db, "EMB-ALERT-001", title="Failed Movie")
    async with aiosqlite.connect(deletion_db) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('discord_alert_deletion_error', 'true')"
        )
        await db.commit()

    with (
        patch("backend.deletion._delete_media", new=AsyncMock(return_value=False)),
        patch("backend.deletion.send_alert", new_callable=AsyncMock) as mock_alert,
        patch.multiple("backend.deletion", **_base_patches()),
    ):
        await run_deletion()

    assert mock_alert.await_count >= 1
    per_item_calls = [c for c in mock_alert.await_args_list if "Failed Movie" in c.args[0]]
    assert per_item_calls, "the per-item failure alert must name the failed title"


# ─── Unhandled exception surfaced, not swallowed ───────────────────────────

async def test_run_deletion_logs_and_counts_an_unhandled_per_item_exception(deletion_db, caplog):
    """An exception escaping _delete_one() (e.g. a DB error in _claim_pending)
    must be logged, not silently treated as '0 deleted' — a systemic bug must
    stay visible."""
    from backend.deletion import run_deletion

    await _seed_item(deletion_db, "EMB-EXC-001")

    with (
        patch("backend.deletion._claim_pending", new=AsyncMock(side_effect=RuntimeError("db exploded"))),
        patch("backend.deletion.send_alert", new_callable=AsyncMock),
        patch.multiple("backend.deletion", **_base_patches()),
        caplog.at_level(logging.ERROR, logger="backend.deletion"),
    ):
        await run_deletion()

    assert any("unhandled exception" in r.message for r in caplog.records)
    async with aiosqlite.connect(deletion_db) as db:
        async with db.execute("SELECT message FROM job_history") as cur:
            (message,) = await cur.fetchone()
    assert "0 deleted" in message, "the exception must not be counted as a success"


# ─── Batch failure-threshold alert ─────────────────────────────────────────

async def test_run_deletion_sends_threshold_alert_when_errors_reach_it(deletion_db):
    """When the error count in a single run reaches discord_alert_error_threshold,
    a distinct batch-level alert must fire (in addition to any per-item ones)."""
    from backend.deletion import run_deletion

    await _seed_item(deletion_db, "EMB-BATCH-001")
    async with aiosqlite.connect(deletion_db) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('discord_alert_error_threshold', '1')"
        )
        await db.commit()

    with (
        patch("backend.deletion._delete_media", new=AsyncMock(return_value=False)),
        patch("backend.deletion.send_alert", new_callable=AsyncMock) as mock_alert,
        patch.multiple("backend.deletion", **_base_patches()),
    ):
        await run_deletion()

    threshold_calls = [c for c in mock_alert.await_args_list if "suppressions en échec" in c.args[0]]
    assert threshold_calls, "reaching the error threshold must send the batch alert"


# ─── Hard timeout / generic exception job outcomes ─────────────────────────

async def test_run_deletion_records_timeout_status_on_hard_cap(deletion_db):
    """If the 1h hard cap trips, the job must finish with status='error' and
    message='timeout' — never left hanging or reported as success."""
    from backend.deletion import run_deletion
    import asyncio as _asyncio

    class _ImmediateTimeout:
        async def __aenter__(self):
            raise TimeoutError()

        async def __aexit__(self, *exc):
            return False

    with patch.object(_asyncio, "timeout", lambda seconds: _ImmediateTimeout()):
        await run_deletion()

    async with aiosqlite.connect(deletion_db) as db:
        async with db.execute("SELECT status, message FROM job_history") as cur:
            row = await cur.fetchone()
    assert row == ("error", "timeout")


async def test_run_deletion_records_error_status_on_unexpected_exception(deletion_db):
    """A genuine bug (e.g. the pending-queue query itself failing) must be
    caught, logged, and reflected in job_history — never crash the scheduler."""
    from backend.deletion import run_deletion

    with patch("backend.deletion.get_pending_queue", new=AsyncMock(side_effect=RuntimeError("boom"))):
        await run_deletion()

    async with aiosqlite.connect(deletion_db) as db:
        async with db.execute("SELECT status, message FROM job_history") as cur:
            row = await cur.fetchone()
    assert row[0] == "error"
    assert "boom" in row[1]


# ─── _run_deletion_guarded — multi-worker lock race ────────────────────────

async def test_run_deletion_guarded_swallows_lock_not_available(deletion_db):
    """In multi-worker mode, another worker already holding the advisory lock
    must result in a silent skip (starvation-checked), never an exception
    escaping to the scheduler."""
    from backend.deletion import _run_deletion_guarded, _deletion_lock
    from backend._lock_backend import LockNotAvailable

    with (
        patch.object(_deletion_lock, "acquire", new=AsyncMock(side_effect=LockNotAvailable("held elsewhere"))),
        patch("backend.deletion.warn_if_job_starved", new=AsyncMock()) as mock_warn,
    ):
        await _run_deletion_guarded()  # must not raise

    mock_warn.assert_awaited_once()
    assert mock_warn.await_args.args[0] == "deletion_check"


# ─── reset_stale_deleting ───────────────────────────────────────────────────

async def test_reset_stale_deleting_recovers_items_stuck_after_a_crash(deletion_db, caplog):
    """Items left in 'deleting' by a crash mid-deletion must be recovered back
    to 'pending' so the next run retries them, instead of being stuck forever."""
    from backend.deletion import reset_stale_deleting

    await _seed_item(deletion_db, "EMB-STUCK-001")
    async with aiosqlite.connect(deletion_db) as db:
        await db.execute("UPDATE media_queue SET status='deleting' WHERE emby_id='EMB-STUCK-001'")
        await db.commit()

    with caplog.at_level(logging.WARNING, logger="backend.deletion"):
        n = await reset_stale_deleting()

    assert n == 1
    async with aiosqlite.connect(deletion_db) as db:
        async with db.execute(
            "SELECT status FROM media_queue WHERE emby_id='EMB-STUCK-001'"
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == "pending"
    assert any("Recovered" in r.message for r in caplog.records)


async def test_reset_stale_deleting_is_a_noop_when_nothing_stuck(deletion_db):
    """No stuck items → returns 0, no crash."""
    from backend.deletion import reset_stale_deleting

    n = await reset_stale_deleting()
    assert n == 0
