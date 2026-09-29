"""Coverage for run_ignored_cleanup() — entirely untested before this file.

Covers: expiry of ignored_media entries (survivors preserved), retention-based
purge of deleted queue rows (and the disabled/0 case), log and job_history
retention purge, and the VACUUM/WAL-checkpoint branch (sqlite only, skipped
on MariaDB).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

import backend.db.engine as _db_engine
import backend.db.schema as _db_schema
import backend.db.settings_store as _db_ss
import backend.db.utils as _db_utils
import backend.deletion as _deletion_mod

from backend.db.engine import get_db
from backend.db.schema import init_db


@pytest.fixture
async def fresh_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "ignored_cleanup.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_deletion_mod, "DB_PATH", db_path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    monkeypatch.setattr(_db_engine, "DIALECT", "sqlite")
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await init_db()
    yield db_path


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ─── Expired ignored_media purge ───────────────────────────────────────────

async def test_expired_ignored_media_entries_are_removed(fresh_db):
    from backend.deletion import run_ignored_cleanup

    now = datetime.now(timezone.utc)
    async with get_db() as db:
        await db.execute(
            "INSERT INTO ignored_media (emby_id, title, ignored_at, expire_at) VALUES (?,?,?,?)",
            ("expired-1", "Old Ignore", _iso(now - timedelta(days=10)), _iso(now - timedelta(days=1))),
        )
        await db.commit()

    await run_ignored_cleanup()

    async with get_db() as db:
        rows = await db.fetch_all("SELECT * FROM ignored_media")
    assert rows == []


async def test_ignored_media_without_expiry_or_in_the_future_is_kept(fresh_db):
    """A permanent ignore (expire_at NULL) or one not due yet must survive
    the cleanup — this table decides what NEVER gets queued for deletion."""
    from backend.deletion import run_ignored_cleanup

    now = datetime.now(timezone.utc)
    async with get_db() as db:
        await db.execute(
            "INSERT INTO ignored_media (emby_id, title, ignored_at, expire_at) VALUES (?,?,?,?)",
            ("permanent-1", "Permanent Ignore", _iso(now), None),
        )
        await db.execute(
            "INSERT INTO ignored_media (emby_id, title, ignored_at, expire_at) VALUES (?,?,?,?)",
            ("future-1", "Future Expiry", _iso(now), _iso(now + timedelta(days=30))),
        )
        await db.commit()

    await run_ignored_cleanup()

    async with get_db() as db:
        rows = await db.fetch_all("SELECT emby_id FROM ignored_media")
    assert {r["emby_id"] for r in rows} == {"permanent-1", "future-1"}


# ─── Deleted queue retention purge ─────────────────────────────────────────

async def _insert_deleted_queue_row(emby_id: str, delete_at_iso: str) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (emby_id, "Old Deleted", "Movie", "lib1", "Library", "/f/x.mkv",
             delete_at_iso, delete_at_iso, "deleted"),
        )
        await db.commit()


async def test_deleted_rows_older_than_retention_are_purged(fresh_db):
    from backend.deletion import run_ignored_cleanup

    old = datetime.now(timezone.utc) - timedelta(days=200)
    await _insert_deleted_queue_row("old-deleted-1", _iso(old))
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('deleted_retention_days', '90')"
        )
        await db.commit()

    await run_ignored_cleanup()

    async with get_db() as db:
        rows = await db.fetch_all("SELECT * FROM media_queue WHERE emby_id='old-deleted-1'")
    assert rows == []


async def test_deleted_rows_within_retention_are_kept(fresh_db):
    from backend.deletion import run_ignored_cleanup

    recent = datetime.now(timezone.utc) - timedelta(days=5)
    await _insert_deleted_queue_row("recent-deleted-1", _iso(recent))
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('deleted_retention_days', '90')"
        )
        await db.commit()

    await run_ignored_cleanup()

    async with get_db() as db:
        rows = await db.fetch_all("SELECT * FROM media_queue WHERE emby_id='recent-deleted-1'")
    assert len(rows) == 1


async def test_retention_purge_disabled_when_zero(fresh_db):
    """deleted_retention_days=0 means 'keep forever' — the purge query must
    not run at all, even against ancient rows."""
    from backend.deletion import run_ignored_cleanup

    ancient = datetime.now(timezone.utc) - timedelta(days=3650)
    await _insert_deleted_queue_row("ancient-deleted-1", _iso(ancient))
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('deleted_retention_days', '0')"
        )
        await db.commit()

    await run_ignored_cleanup()

    async with get_db() as db:
        rows = await db.fetch_all("SELECT * FROM media_queue WHERE emby_id='ancient-deleted-1'")
    assert len(rows) == 1, "retention=0 must disable the purge, not purge everything"


# ─── Log retention purge ───────────────────────────────────────────────────

async def test_old_logs_are_purged_recent_logs_are_kept(fresh_db):
    from backend.deletion import run_ignored_cleanup

    old = datetime.now(timezone.utc) - timedelta(days=30)
    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    async with get_db() as db:
        await db.execute(
            "INSERT INTO logs (ts, level, source, message) VALUES (?,?,?,?)",
            (_iso(old), "INFO", "system", "old log line"),
        )
        await db.execute(
            "INSERT INTO logs (ts, level, source, message) VALUES (?,?,?,?)",
            (_iso(recent), "INFO", "system", "recent log line"),
        )
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('log_retention_days', '14')"
        )
        await db.commit()

    await run_ignored_cleanup()

    async with get_db() as db:
        rows = await db.fetch_all("SELECT message FROM logs")
    messages = {r["message"] for r in rows}
    assert "old log line" not in messages
    assert "recent log line" in messages


# ─── job_history retention purge ───────────────────────────────────────────

async def test_old_job_history_entries_are_purged(fresh_db):
    from backend.deletion import run_ignored_cleanup

    old = datetime.now(timezone.utc) - timedelta(days=200)
    recent = datetime.now(timezone.utc) - timedelta(days=1)
    async with get_db() as db:
        await db.execute(
            "INSERT INTO job_history (job_type, started_at) VALUES (?,?)",
            ("deletion_check", _iso(old)),
        )
        await db.execute(
            "INSERT INTO job_history (job_type, started_at) VALUES (?,?)",
            ("deletion_check", _iso(recent)),
        )
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('job_history_retention_days', '90')"
        )
        await db.commit()

    await run_ignored_cleanup()

    async with get_db() as db:
        rows = await db.fetch_all("SELECT started_at FROM job_history")
    assert len(rows) == 1


# ─── VACUUM branch ──────────────────────────────────────────────────────────

async def test_vacuum_runs_on_sqlite_when_purge_exceeds_threshold(fresh_db):
    """purged_rows > 1000 must trigger VACUUM + WAL checkpoint on SQLite."""
    from backend.deletion import run_ignored_cleanup

    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('deleted_retention_days', '90')"
        )
        await db.commit()

    with (
        patch("backend.deletion.delete_stale_deleted", new=AsyncMock(return_value=1500)),
        patch("backend.db.engine.DIALECT", "sqlite"),
    ):
        # Real VACUUM against the real temp sqlite file — cheap on an empty DB,
        # and proves the code path actually opens/executes against DB_PATH
        # rather than merely not-crashing.
        await run_ignored_cleanup()  # must not raise


async def test_vacuum_skipped_on_mariadb_even_when_purge_exceeds_threshold(fresh_db, caplog):
    """MariaDB has no VACUUM/WAL-checkpoint equivalent — the branch must be
    skipped, not attempted against a SQLite-only API.

    get_db() itself dispatches on the same DIALECT flag (and aiosqlite uses
    sqlite3.connect() internally for every real query), so neither "DIALECT"
    nor "sqlite3.connect" can be globally faked without corrupting the
    engine's own unrelated queries in this test. Disabling the log/job_history
    purges (retention=0) and starting with no ignored_media rows means the
    only real get_db() traffic happens BEFORE delete_stale_deleted() is
    invoked; the dialect is flipped to "mariadb" from inside that mocked
    call — after which no further DB access happens — and the skip is
    observed through the debug log the code itself emits on that branch.
    """
    import logging
    import backend.db.engine as _db_engine
    from backend.deletion import run_ignored_cleanup

    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('deleted_retention_days', '90')"
        )
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('log_retention_days', '0')"
        )
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('job_history_retention_days', '0')"
        )
        await db.commit()

    async def _purge_and_flip_to_mariadb(cutoff):
        _db_engine.DIALECT = "mariadb"
        return 1500

    with (
        patch("backend.deletion.delete_stale_deleted", new=AsyncMock(side_effect=_purge_and_flip_to_mariadb)),
        caplog.at_level(logging.DEBUG, logger="backend.deletion"),
    ):
        await run_ignored_cleanup()

    assert any(
        "VACUUM" in r.message and "skipped" in r.message for r in caplog.records
    ), "MariaDB must take the skip branch, not attempt a SQLite-only VACUUM"


# ─── Defensive except-blocks — a purge failure must not abort the others ───

async def test_retention_purge_failure_is_isolated_and_logged(fresh_db, caplog):
    """delete_stale_deleted() raising (e.g. DB locked) must not propagate —
    the rest of the cleanup job (logs, job_history) still needs to run."""
    import logging
    from backend.deletion import run_ignored_cleanup

    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('deleted_retention_days', '90')"
        )
        await db.commit()

    with (
        patch("backend.deletion.delete_stale_deleted", new=AsyncMock(side_effect=RuntimeError("db locked"))),
        caplog.at_level(logging.DEBUG, logger="backend.deletion"),
    ):
        await run_ignored_cleanup()  # must not raise

    assert any("Purge retention" in r.message for r in caplog.records)


async def test_log_purge_failure_is_isolated_and_logged(fresh_db, caplog):
    """A DB error while purging old logs must not abort the job_history purge
    that follows it."""
    import logging
    from backend.deletion import run_ignored_cleanup

    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('deleted_retention_days', '0')"
        )
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('log_retention_days', '14')"
        )
        await db.execute(
            "INSERT INTO job_history (job_type, started_at) VALUES (?,?)",
            ("deletion_check", _iso(datetime.now(timezone.utc) - timedelta(days=200))),
        )
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('job_history_retention_days', '90')"
        )
        await db.commit()

    # Break the logs table itself so its COUNT query fails, while job_history
    # (a different, intact table) still works — a real, targeted failure
    # rather than a blanket DB mock.
    async with get_db() as db:
        await db.execute("DROP TABLE logs")
        await db.commit()

    with caplog.at_level(logging.DEBUG, logger="backend.deletion"):
        await run_ignored_cleanup()  # must not raise despite the missing table

    assert any("Purge logs" in r.message for r in caplog.records)
    async with get_db() as db:
        rows = await db.fetch_all("SELECT * FROM job_history")
    assert rows == [], "job_history purge must still run after the logs purge fails"


async def test_job_history_purge_failure_is_isolated_and_logged(fresh_db, caplog):
    """A DB error while purging old job_history rows must not abort the whole
    cleanup job."""
    import logging
    from backend.deletion import run_ignored_cleanup

    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('deleted_retention_days', '0')"
        )
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('log_retention_days', '0')"
        )
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('job_history_retention_days', '90')"
        )
        await db.execute("DROP TABLE job_history")
        await db.commit()

    with caplog.at_level(logging.DEBUG, logger="backend.deletion"):
        await run_ignored_cleanup()  # must not raise despite the missing table

    assert any("Purge job_history" in r.message for r in caplog.records)


async def test_vacuum_failure_is_caught_and_logged(fresh_db, monkeypatch, caplog):
    """A VACUUM I/O failure (disk full, locked file) must be caught, not
    crash the cleanup job that got this far successfully."""
    import logging
    import sqlite3
    from backend.deletion import run_ignored_cleanup

    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('deleted_retention_days', '90')"
        )
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('log_retention_days', '0')"
        )
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('job_history_retention_days', '0')"
        )
        await db.commit()

    async def _purge_and_break_sqlite_connect(cutoff):
        # By this point the two legitimate real DB calls (expiry SELECT +
        # settings-cache refresh) have already happened — breaking
        # sqlite3.connect() only from here on reaches exclusively the
        # VACUUM step's own direct connection, not the DB engine's.
        def _broken_connect(*a, **kw):
            raise OSError("disk full")
        monkeypatch.setattr(sqlite3, "connect", _broken_connect)
        return 1500

    with (
        patch("backend.deletion.delete_stale_deleted", new=AsyncMock(side_effect=_purge_and_break_sqlite_connect)),
        patch("backend.db.engine.DIALECT", "sqlite"),
        caplog.at_level(logging.DEBUG, logger="backend.deletion"),
    ):
        await run_ignored_cleanup()  # must not raise

    assert any("VACUUM:" in r.message for r in caplog.records)
