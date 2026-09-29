"""Coverage tests for backend/arr_clients/seerr.py — Seerr/Overseerr client.

Priority: seerr_delete_request must target the exact media id; the circuit
breaker path (build_seerr_request_cache) must surface CircuitOpenError as
ArrClientError so callers degrade the same way for both.
"""
import os

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")

import httpx
import pytest
from pytest_httpx import HTTPXMock

import backend.arr_clients.seerr as seerr
from backend.arr_clients.seerr import _seerr_config as _real_seerr_config
from backend.arr_clients import circuit_breaker as cb_mod
from backend.exceptions import ArrClientError

SEERR_URL = "http://seerr.test:5055"
SEERR_KEY = "seerr-test-key"


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    async def _config():
        return SEERR_URL, SEERR_KEY
    monkeypatch.setattr(seerr, "_seerr_config", _config)


@pytest.fixture(autouse=True)
def _reset_breaker_registry():
    cb_mod._registry.clear()
    yield
    cb_mod._registry.clear()


# ─── _seerr_config ──────────────────────────────────────────────────────────────

async def test_seerr_config_strips_trailing_slash(monkeypatch):
    async def _get_setting(key):
        return {"seerr_url": "http://raw:5055/", "seerr_api_key": "rawkey"}.get(key)
    monkeypatch.setattr(seerr, "get_setting", _get_setting)

    url, key = await _real_seerr_config()
    assert url == "http://raw:5055"
    assert key == "rawkey"


async def test_seerr_config_defaults_to_empty(monkeypatch):
    async def _get_setting(key):
        return None
    monkeypatch.setattr(seerr, "get_setting", _get_setting)
    url, key = await _real_seerr_config()
    assert (url, key) == ("", "")


# ─── test_seerr ──────────────────────────────────────────────────────────────────

async def test_test_seerr_not_configured(monkeypatch):
    async def _config():
        return "", ""
    monkeypatch.setattr(seerr, "_seerr_config", _config)
    ok, msg = await seerr.test_seerr()
    assert ok is False
    assert msg == "Non configuré"


async def test_test_seerr_success(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SEERR_URL}/api/v1/status", json={"version": "1.33.0"})
    ok, msg = await seerr.test_seerr()
    assert ok is True
    assert msg == "Seerr 1.33.0"


async def test_test_seerr_http_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SEERR_URL}/api/v1/status", status_code=401)
    ok, msg = await seerr.test_seerr()
    assert ok is False
    assert msg == "HTTP 401"


async def test_test_seerr_network_exception(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    ok, msg = await seerr.test_seerr()
    assert ok is False
    assert "refused" in msg


# ─── seerr_get_users ────────────────────────────────────────────────────────────

async def test_get_users_returns_empty_without_creds(monkeypatch):
    async def _config():
        return "", ""
    monkeypatch.setattr(seerr, "_seerr_config", _config)
    assert await seerr.seerr_get_users() == []


async def test_get_users_merges_hygie_and_seerr_discord_ids(httpx_mock: HTTPXMock):
    """No seerr_user_rules table exists (in-memory DB, unmigrated) — the DB
    lookup must fail silently, leaving hygie_mappings empty, and the Seerr
    notification-settings discordIds must still be picked up."""
    httpx_mock.add_response(
        url=f"{SEERR_URL}/api/v1/user?take=100&skip=0",
        json={"results": [{"id": 1, "displayName": "Alice"}], "pageInfo": {"results": 1}},
    )
    httpx_mock.add_response(
        url=f"{SEERR_URL}/api/v1/user/1/settings/notifications",
        json={"discordIds": ["111222333"]},
    )
    users = await seerr.seerr_get_users()
    assert users == [{
        "id": 1, "username": "Alice",
        "discord_id": "111222333",
        "discord_id_seerr": "111222333",
        "discord_id_hygie": "",
    }]


async def test_get_users_falls_back_to_username_then_email_for_name(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{SEERR_URL}/api/v1/user?take=100&skip=0",
        json={"results": [{"id": 2, "username": "bob"}], "pageInfo": {"results": 1}},
    )
    httpx_mock.add_response(
        url=f"{SEERR_URL}/api/v1/user/2/settings/notifications", status_code=500,
    )
    users = await seerr.seerr_get_users()
    assert users[0]["username"] == "bob"
    assert users[0]["discord_id"] == ""


async def test_get_users_hygie_mapping_takes_priority_over_seerr(monkeypatch, tmp_path, httpx_mock: HTTPXMock):
    """A real seerr_user_rules row with a Hygie-configured discord_id must
    win over the auto-detected Seerr discordIds for the same user."""
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _db_ss
    import backend.db.media_servers as _db_ms
    import backend.db.schema as _db_schema
    import backend.db.engine as _db_engine

    db_path = str(tmp_path / "seerr_test.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ms, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    await _db_schema.init_db()

    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO seerr_user_rules "
            "(name, seerr_user_id, seerr_username, library_id, discord_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ("rule", 1, "Alice", "*", "999888777"),
        )
        await db.commit()

    httpx_mock.add_response(
        url=f"{SEERR_URL}/api/v1/user?take=100&skip=0",
        json={"results": [{"id": 1, "displayName": "Alice"}], "pageInfo": {"results": 1}},
    )
    httpx_mock.add_response(
        url=f"{SEERR_URL}/api/v1/user/1/settings/notifications",
        json={"discordIds": ["111222333"]},
    )

    users = await seerr.seerr_get_users()
    assert users == [{
        "id": 1, "username": "Alice",
        "discord_id": "999888777",
        "discord_id_seerr": "111222333",
        "discord_id_hygie": "999888777",
    }]


async def test_get_users_swallows_notification_lookup_exception(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{SEERR_URL}/api/v1/user?take=100&skip=0",
        json={"results": [{"id": 3}], "pageInfo": {"results": 1}},
    )
    httpx_mock.add_exception(
        httpx.ConnectError("down"), url=f"{SEERR_URL}/api/v1/user/3/settings/notifications"
    )
    users = await seerr.seerr_get_users()
    assert users[0]["username"] == "User #3"
    assert users[0]["discord_id"] == ""


async def test_get_users_returns_empty_on_outer_exception(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(httpx.ConnectError("down"), url=f"{SEERR_URL}/api/v1/user?take=100&skip=0")
    assert await seerr.seerr_get_users() == []


# ─── build_seerr_request_cache ─────────────────────────────────────────────────

async def test_build_request_cache_returns_empty_without_creds(monkeypatch):
    async def _config():
        return "", ""
    monkeypatch.setattr(seerr, "_seerr_config", _config)
    assert await seerr.build_seerr_request_cache() == {}


async def test_build_request_cache_first_request_wins_per_tmdb_id(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{SEERR_URL}/api/v1/request?sort=added&filter=all&take=100&skip=0",
        json={
            "results": [
                {"media": {"tmdbId": 42, "id": 900}, "requestedBy": {"displayName": "First"}},
                {"media": {"tmdbId": 42, "id": 901}, "requestedBy": {"displayName": "Second"}},
            ],
            "pageInfo": {"results": 2},
        },
    )
    cache = await seerr.build_seerr_request_cache()
    assert cache["42"] == {"seerr_id": 900, "user_id": None, "username": "First"}


async def test_build_request_cache_skips_requests_without_tmdb_id(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{SEERR_URL}/api/v1/request?sort=added&filter=all&take=100&skip=0",
        json={"results": [{"media": {}}], "pageInfo": {"results": 1}},
    )
    cache = await seerr.build_seerr_request_cache()
    assert cache == {}


async def test_build_request_cache_wraps_generic_exception_as_arr_client_error(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(
        httpx.ConnectError("down"), url=f"{SEERR_URL}/api/v1/request?sort=added&filter=all&take=100&skip=0"
    )
    with pytest.raises(ArrClientError):
        await seerr.build_seerr_request_cache()


async def test_build_request_cache_reraises_arr_client_error_unwrapped(monkeypatch):
    async def _get_breaker_call(coro_factory):
        raise ArrClientError("already typed")

    class _FakeBreaker:
        async def call(self, coro_factory):
            raise ArrClientError("already typed")

    monkeypatch.setattr(cb_mod, "get_breaker", lambda name: _FakeBreaker())
    with pytest.raises(ArrClientError, match="already typed"):
        await seerr.build_seerr_request_cache()


async def test_build_request_cache_open_breaker_raises_arr_client_error(monkeypatch):
    breaker = cb_mod.get_breaker("seerr", failure_threshold=1)
    breaker._state = cb_mod.CircuitBreaker.OPEN
    import time as _time
    breaker._last_failure_ts = _time.monotonic()

    with pytest.raises(ArrClientError):
        await seerr.build_seerr_request_cache()


# ─── seerr_find_request_by_tmdb ────────────────────────────────────────────────

async def test_find_request_by_tmdb_returns_none_without_creds_or_id(monkeypatch):
    assert await seerr.seerr_find_request_by_tmdb("") is None
    async def _config():
        return "", ""
    monkeypatch.setattr(seerr, "_seerr_config", _config)
    assert await seerr.seerr_find_request_by_tmdb("42") is None


async def test_find_request_by_tmdb_matches_and_extracts_username(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{SEERR_URL}/api/v1/request?sort=added&filter=all&take=100&skip=0",
        json={
            "results": [{
                "media": {"tmdbId": 77, "id": 950},
                "requestedBy": {"email": "user@example.com"},
            }],
            "pageInfo": {"results": 1},
        },
    )
    result = await seerr.seerr_find_request_by_tmdb("77")
    assert result == {"seerr_id": 950, "user_id": None, "username": "user@example.com"}


async def test_find_request_by_tmdb_no_match_returns_none(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{SEERR_URL}/api/v1/request?sort=added&filter=all&take=100&skip=0",
        json={"results": [{"media": {"tmdbId": 1}}], "pageInfo": {"results": 1}},
    )
    assert await seerr.seerr_find_request_by_tmdb("999") is None


async def test_find_request_by_tmdb_swallows_exception(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(
        httpx.ConnectError("down"),
        url=f"{SEERR_URL}/api/v1/request?sort=added&filter=all&take=100&skip=0",
    )
    assert await seerr.seerr_find_request_by_tmdb("1") is None


# ─── seerr_delete_request — CRITICAL: exact media id ───────────────────────────

async def test_delete_request_targets_exact_media_id(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SEERR_URL}/api/v1/media/555", status_code=200)
    ok = await seerr.seerr_delete_request(555)
    assert ok is True
    req = httpx_mock.get_requests()[0]
    assert req.url.path == "/api/v1/media/555"
    assert req.headers.get("x-api-key") == SEERR_KEY


async def test_delete_request_returns_false_without_creds_or_id(monkeypatch):
    assert await seerr.seerr_delete_request(0) is False
    async def _config():
        return "", ""
    monkeypatch.setattr(seerr, "_seerr_config", _config)
    assert await seerr.seerr_delete_request(1) is False


async def test_delete_request_returns_false_on_non_2xx(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SEERR_URL}/api/v1/media/555", status_code=404)
    assert await seerr.seerr_delete_request(555) is False


async def test_delete_request_returns_false_on_exception(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(httpx.ConnectError("down"))
    assert await seerr.seerr_delete_request(555) is False
