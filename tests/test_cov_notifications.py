"""Coverage for backend/notifications.py — threshold-based Discord
notifications for pending deletions. Untested before this file: no existing
test exercises _parse_thresholds() or _send_pending_notifications() directly
(other tests only mock '_send_pending_notifications' out entirely as a
dependency of the scanner).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

import backend.db.engine as _db_engine
import backend.db.schema as _db_schema
import backend.db.settings_store as _db_ss
import backend.db.utils as _db_utils

from backend.db.engine import get_db
from backend.db.schema import init_db
from backend.notifications import _parse_thresholds


@pytest.fixture
async def fresh_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "notifications.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    monkeypatch.setattr(_db_engine, "DIALECT", "sqlite")
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await init_db()
    yield db_path


def _iso(dt: datetime) -> str:
    return dt.isoformat()


async def _insert_pending(db_path, emby_id: str, delete_at_iso: str, title: str = "Movie") -> int:
    async with get_db() as db:
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (emby_id, title, "Movie", "lib1", "Library", "/f/x.mkv",
             _iso(datetime.now(timezone.utc)), delete_at_iso, "pending"),
        )
        await db.commit()
        row = await db.fetch_one("SELECT id FROM media_queue WHERE emby_id=?", (emby_id,))
    return row["id"]


# ─── _parse_thresholds ──────────────────────────────────────────────────────

def test_parse_thresholds_sorts_descending_and_dedupes():
    assert _parse_thresholds("1,7,3") == [7, 3, 1]


def test_parse_thresholds_dedupes_repeated_values():
    assert _parse_thresholds("7,7,1") == [7, 1]


def test_parse_thresholds_handles_whitespace_around_values():
    assert _parse_thresholds(" 7 , 1 ") == [7, 1]


def test_parse_thresholds_ignores_non_numeric_tokens_silently():
    """Non-digit tokens are filtered out by the isdigit() guard, not by the
    except clause — so 'abc' does not trigger the documented [7, 1] fallback,
    it produces an empty list. See BUGS SUSPECTÉS in the final report."""
    assert _parse_thresholds("abc,xyz") == []


def test_parse_thresholds_falls_back_to_default_only_on_a_real_exception():
    """The [7, 1] fallback is only reached when .split() itself raises
    (e.g. a non-string value from a corrupted setting), not for merely
    malformed content."""
    assert _parse_thresholds(None) == [7, 1]


# ─── _send_pending_notifications — basic send + persistence ───────────────

async def test_sends_notification_for_item_within_threshold_window(fresh_db):
    from backend.notifications import _send_pending_notifications

    due_soon = datetime.now(timezone.utc) + timedelta(days=1)
    media_id = await _insert_pending(fresh_db, "emb-1", _iso(due_soon), title="Inception")

    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('discord_notif_thresholds', '1')"
        )
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('dry_run', 'false')")
        await db.commit()

    with patch("backend.notifications.send_notification", new=AsyncMock(return_value=True)) as mock_send:
        await _send_pending_notifications()

    mock_send.assert_awaited_once()
    sent_items, threshold_key = mock_send.await_args.args[0], mock_send.await_args.args[1]
    assert [i["id"] for i in sent_items] == [media_id]
    assert threshold_key == "1d"

    async with get_db() as db:
        row = await db.fetch_one(
            "SELECT * FROM notifications WHERE media_id=? AND threshold='1d'", (media_id,)
        )
    assert row is not None, "a sent notification must be recorded to prevent duplicates"


async def test_item_outside_threshold_window_is_not_notified(fresh_db):
    """An item due in 10 days must not trigger a 1-day-threshold notification."""
    from backend.notifications import _send_pending_notifications

    due_later = datetime.now(timezone.utc) + timedelta(days=10)
    await _insert_pending(fresh_db, "emb-2", _iso(due_later))

    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('discord_notif_thresholds', '1')"
        )
        await db.commit()

    with patch("backend.notifications.send_notification", new=AsyncMock()) as mock_send:
        await _send_pending_notifications()

    mock_send.assert_not_awaited()


async def test_already_notified_item_is_not_notified_again(fresh_db):
    """Idempotency: an item that already has a notifications row for this
    threshold must be excluded from the next run's candidate list."""
    from backend.notifications import _send_pending_notifications

    due_soon = datetime.now(timezone.utc) + timedelta(days=1)
    media_id = await _insert_pending(fresh_db, "emb-3", _iso(due_soon))

    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('discord_notif_thresholds', '1')"
        )
        await db.execute(
            "INSERT INTO notifications (media_id, threshold) VALUES (?, '1d')", (media_id,)
        )
        await db.commit()

    with patch("backend.notifications.send_notification", new=AsyncMock()) as mock_send:
        await _send_pending_notifications()

    mock_send.assert_not_awaited()


async def test_each_threshold_fires_independently_for_the_same_item(fresh_db):
    """An item due tomorrow falls within BOTH the 7d and 1d cutoffs — each
    threshold must notify independently (an item can receive both)."""
    from backend.notifications import _send_pending_notifications

    due_soon = datetime.now(timezone.utc) + timedelta(days=1)
    media_id = await _insert_pending(fresh_db, "emb-4", _iso(due_soon))

    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('discord_notif_thresholds', '7,1')"
        )
        await db.commit()

    with patch("backend.notifications.send_notification", new=AsyncMock(return_value=True)) as mock_send:
        await _send_pending_notifications()

    thresholds_sent = {c.args[1] for c in mock_send.await_args_list}
    assert thresholds_sent == {"7d", "1d"}

    async with get_db() as db:
        rows = await db.fetch_all("SELECT threshold FROM notifications WHERE media_id=?", (media_id,))
    assert {r["threshold"] for r in rows} == {"7d", "1d"}


# ─── send failure — must not mark as notified ──────────────────────────────

async def test_failed_send_does_not_record_a_notification_marker(fresh_db, caplog):
    """If send_notification() reports failure, no notifications row is
    written — the item must be retried on the next cycle, not silently
    treated as already-notified."""
    import logging
    from backend.notifications import _send_pending_notifications

    due_soon = datetime.now(timezone.utc) + timedelta(days=1)
    media_id = await _insert_pending(fresh_db, "emb-5", _iso(due_soon))

    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('discord_notif_thresholds', '1')"
        )
        await db.commit()

    with (
        patch("backend.notifications.send_notification", new=AsyncMock(return_value=False)),
        caplog.at_level(logging.WARNING, logger="backend.notifications"),
    ):
        await _send_pending_notifications()

    async with get_db() as db:
        row = await db.fetch_one("SELECT * FROM notifications WHERE media_id=?", (media_id,))
    assert row is None
    assert any("will retry" in r.message for r in caplog.records)


# ─── dry_run passthrough ────────────────────────────────────────────────────

async def test_dry_run_flag_is_passed_through_to_send_notification(fresh_db):
    from backend.notifications import _send_pending_notifications

    due_soon = datetime.now(timezone.utc) + timedelta(days=1)
    await _insert_pending(fresh_db, "emb-6", _iso(due_soon))

    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('discord_notif_thresholds', '1')"
        )
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('dry_run', 'true')")
        await db.commit()

    with patch("backend.notifications.send_notification", new=AsyncMock(return_value=True)) as mock_send:
        await _send_pending_notifications()

    assert mock_send.await_args.kwargs.get("dry_run") is True


# ─── Resilience: an unexpected exception must not propagate ───────────────

async def test_unexpected_exception_is_swallowed_not_propagated(fresh_db, caplog):
    """A failure while dispatching a threshold's notification (inside the
    per-threshold loop) must not crash run_deletion()'s caller — it is
    logged and the function returns normally, so the remaining scheduled
    work in the job that called it still runs."""
    import logging
    from backend.notifications import _send_pending_notifications

    due_soon = datetime.now(timezone.utc) + timedelta(days=1)
    await _insert_pending(fresh_db, "emb-exc-1", _iso(due_soon))

    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('discord_notif_thresholds', '1')"
        )
        await db.commit()

    with (
        patch("backend.notifications.send_notification", new=AsyncMock(side_effect=RuntimeError("discord down"))),
        caplog.at_level(logging.WARNING, logger="backend.notifications"),
    ):
        await _send_pending_notifications()  # must not raise

    assert any("_send_pending_notifications" in r.message for r in caplog.records)
