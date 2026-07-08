"""DiscordNotifyStep must persist the 'now' marker itself.

Previously the marker was written only by update_queue_status() for the
deleted/deleting statuses, after the pipeline. A deletion that failed (status
'error') then retried re-sent a duplicate "supprimé" Discord message because the
idempotency guard had nothing to match. These tests lock the marker to the step.
"""
import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.pop("DATABASE_URL", None)

from backend.deletion_pipeline import DeletionContext, DiscordNotifyStep


class _FakeDB:
    def __init__(self, existing_row=None):
        self._row = existing_row
        self.inserts = []

    async def fetch_one(self, sql, params=()):
        return self._row

    async def execute(self, sql, params=()):
        self.inserts.append((sql, params))

    async def commit(self):
        pass


def _patch_db(fake):
    @asynccontextmanager
    async def _cm():
        yield fake
    return patch("backend.db.engine.get_db", _cm)


async def test_records_now_marker_after_sending():
    fake = _FakeDB(existing_row=None)
    ctx = DeletionContext(item={"id": 42, "title": "X", "emby_id": "e42"}, dry_run=False)
    with _patch_db(fake), patch(
        "backend.discord_client.send_notification", new=AsyncMock(return_value=True)
    ) as mock_send:
        await DiscordNotifyStep().execute(ctx)

    mock_send.assert_awaited_once()
    assert any(
        "notifications" in sql and params == (42, "now")
        for sql, params in fake.inserts
    ), f"expected a 'now' marker insert, got {fake.inserts}"


async def test_skips_when_marker_already_present():
    fake = _FakeDB(existing_row={"1": 1})
    ctx = DeletionContext(item={"id": 42, "title": "X", "emby_id": "e42"}, dry_run=False)
    with _patch_db(fake), patch(
        "backend.discord_client.send_notification", new=AsyncMock(return_value=True)
    ) as mock_send:
        await DiscordNotifyStep().execute(ctx)

    mock_send.assert_not_awaited()


async def test_dry_run_sends_nothing():
    fake = _FakeDB(existing_row=None)
    ctx = DeletionContext(item={"id": 42, "title": "X", "emby_id": "e42"}, dry_run=True)
    with _patch_db(fake), patch(
        "backend.discord_client.send_notification", new=AsyncMock(return_value=True)
    ) as mock_send:
        await DiscordNotifyStep().execute(ctx)

    mock_send.assert_not_awaited()
    assert fake.inserts == []
