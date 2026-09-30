"""Discord pre-deletion thresholds: shared parser, read fallback, save validation.

Regression: an invalid value ("abc", "7j,1j") used to parse to [] and silently
disabled every pre-deletion Discord alert. Only an explicitly empty value may do that.
"""
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.pop("DATABASE_URL", None)

from backend.db.settings_store import parse_thresholds, resolve_thresholds
from tests.test_v260_fixes import _setup_user, app_client  # noqa: F401  (fixture)


# ─── parse_thresholds (strict) ───────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("7,1", ([7, 1], [])),
    ("1,7,7, 30 ", ([30, 7, 1], [])),
    ("", ([], [])),
    ("   ", ([], [])),
    (None, ([], [])),
    ("7,,1,", ([7, 1], [])),
    ("abc", ([], ["abc"])),
    ("7;1", ([], ["7;1"])),
    ("7j,1j", ([], ["7j", "1j"])),
    ("7,abc,1", ([7, 1], ["abc"])),
    ("0,-3,2.5,3", ([3], ["0", "-3", "2.5"])),
])
def test_parse_thresholds(raw, expected):
    assert parse_thresholds(raw) == expected


# ─── resolve_thresholds (read side) ──────────────────────────────────────────

def test_resolve_empty_disables_without_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert resolve_thresholds("") == []
        assert resolve_thresholds("  ") == []
    assert not caplog.records


@pytest.mark.parametrize("raw", ["abc", "7;1", "7j,1j", "0"])
def test_resolve_no_valid_token_falls_back_with_warning(raw, caplog):
    with caplog.at_level(logging.WARNING):
        assert resolve_thresholds(raw) == [7, 1]
    assert any(r.levelno == logging.WARNING and raw in r.getMessage() for r in caplog.records)


def test_resolve_ignores_invalid_token_keeps_rest_with_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert resolve_thresholds("7,abc,1") == [7, 1]
        assert resolve_thresholds("30,x") == [30]
    assert any("abc" in r.getMessage() for r in caplog.records)


def test_resolve_valid_no_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert resolve_thresholds("14,3,1") == [14, 3, 1]
    assert not caplog.records


# ─── consumers use the shared parser ─────────────────────────────────────────

class _FakeDB:
    def __init__(self):
        self.inserts = []

    async def fetch_one(self, sql, params=()):
        return {"id": 42}

    async def execute(self, sql, params=()):
        self.inserts.append(params)

    async def commit(self):
        pass


async def _premark(raw):
    from backend.scanner import _queue_entry
    fake = _FakeDB()

    @asynccontextmanager
    async def _cm():
        yield fake

    soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    with patch("backend.db.settings_store.get_setting", AsyncMock(return_value=raw)), \
         patch("backend.db.engine.get_db", _cm):
        await _queue_entry._pre_mark_applicable_thresholds("e1", soon)
    return sorted(p[1] for p in fake.inserts)


async def test_premark_invalid_value_falls_back_to_default_thresholds():
    assert await _premark("abc") == ["1d", "7d"]


async def test_premark_empty_value_marks_nothing():
    assert await _premark("") == []


async def test_premark_partially_valid_value_keeps_valid_tokens():
    assert await _premark("7,abc") == ["7d"]


async def _thresholds_queried_by_send_pending(raw):
    import backend.notifications as notif

    queried = []

    class _DB:
        async def fetch_all(self, sql, params=()):
            queried.append(params[0])
            return []

    @asynccontextmanager
    async def _cm():
        yield _DB()

    with patch.object(notif, "get_setting", AsyncMock(return_value=raw)), \
         patch.object(notif, "get_bool_setting", AsyncMock(return_value=False)), \
         patch.object(notif, "get_db", _cm):
        await notif._send_pending_notifications()
    return queried


async def test_send_pending_uses_default_when_value_invalid():
    assert len(await _thresholds_queried_by_send_pending("abc")) == 2  # 7d and 1d


async def test_send_pending_empty_value_queries_nothing():
    assert await _thresholds_queried_by_send_pending("") == []


# ─── save-side validation ────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["abc", "7;1", "7j,1j", "7,abc,1", "0", ","])
async def test_save_rejects_invalid_thresholds_with_422(app_client, value):
    _, client = app_client
    await _setup_user(client)
    r = await client.post("/api/settings", json={"discord_notif_thresholds": value})
    assert r.status_code == 422, r.text
    assert "discord_notif_thresholds" in r.json()["detail"]


async def test_save_422_lists_every_invalid_token(app_client):
    _, client = app_client
    await _setup_user(client)
    r = await client.post("/api/settings", json={"discord_notif_thresholds": "7j,1j"})
    assert r.status_code == 422
    assert "7j" in r.json()["detail"] and "1j" in r.json()["detail"]


async def test_invalid_save_does_not_persist(app_client):
    _, client = app_client
    await _setup_user(client)
    await client.post("/api/settings", json={"discord_notif_thresholds": "abc"})
    from backend.db.settings_store import get_setting
    assert await get_setting("discord_notif_thresholds") == "7,1"


@pytest.mark.parametrize("value", ["", "  ", "14,3,1", " 7 , 1 "])
async def test_save_accepts_empty_and_valid(app_client, value):
    _, client = app_client
    await _setup_user(client)
    r = await client.post("/api/settings", json={"discord_notif_thresholds": value})
    assert r.status_code == 200, r.text


def test_resolve_non_string_value_falls_back_to_default_with_warning(caplog):
    from backend.db.settings_store import resolve_thresholds
    with caplog.at_level("WARNING"):
        assert resolve_thresholds(12345) == [7, 1]
    assert "aucun seuil valide" in caplog.text
