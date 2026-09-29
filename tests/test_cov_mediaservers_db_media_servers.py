"""Coverage tests for backend/db/media_servers.py — the media-server list
persistence + cache. A stale/empty read here can make every media-server
client silently treat a configured server as absent.
"""
import pytest

import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
import backend.db.media_servers as _db_ms
import backend.db.schema as _db_schema


@pytest.fixture(autouse=True)
async def fresh_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "ms_test.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ms, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    import backend.db.engine as _db_engine
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()


# ─── get_media_servers: empty / missing row ────────────────────────────────────

async def test_get_media_servers_returns_empty_list_when_never_saved():
    """init_db seeds settings.media_servers = '[]' (DEFAULT_SETTINGS), so this
    exercises the decrypt-and-parse path, not the missing-row branch."""
    result = await _db_ms.get_media_servers()
    assert result == []


async def test_get_media_servers_returns_empty_list_when_settings_row_has_no_value():
    """Defensive branch: a settings row for media_servers exists but its value
    is empty/NULL (e.g. a partially-written row) — must not crash, must not
    surface a stale server list either."""
    from backend.db.engine import get_db

    async with get_db() as db:
        await db.execute("UPDATE settings SET value='' WHERE `key`='media_servers'")
        await db.commit()
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0

    result = await _db_ms.get_media_servers()
    assert result == []


async def test_get_media_servers_second_call_within_ttl_served_from_cache(monkeypatch):
    """The second call inside the 30s TTL must not touch the DB at all."""
    await _db_ms.save_media_servers([{"id": "0", "type": "emby"}])

    def _boom():
        raise AssertionError("get_media_servers hit the DB despite a warm cache")

    monkeypatch.setattr(_db_ms, "get_db", _boom)
    result = await _db_ms.get_media_servers()
    assert result == [{"id": "0", "type": "emby"}]


async def test_get_media_servers_returns_saved_servers_after_save():
    await _db_ms.save_media_servers([{"id": "0", "type": "emby", "url": "http://x", "api_key": "k"}])
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0
    result = await _db_ms.get_media_servers()
    assert result == [{"id": "0", "type": "emby", "url": "http://x", "api_key": "k"}]


# ─── get_media_servers: DB failure → never raises ──────────────────────────────

async def test_get_media_servers_returns_empty_list_on_db_error_with_no_prior_cache(monkeypatch):
    async def _boom():
        raise RuntimeError("db unavailable")

    class _BoomCtx:
        async def __aenter__(self):
            raise RuntimeError("db unavailable")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(_db_ms, "get_db", lambda: _BoomCtx())
    result = await _db_ms.get_media_servers()
    assert result == []


async def test_get_media_servers_returns_stale_cache_on_db_error_when_cache_exists(monkeypatch):
    """A transient DB error must not wipe out the last-known-good server list —
    that list is what every Emby/Plex client call depends on to find its host."""
    _db_ms._ms_cache = [{"id": "0", "type": "emby"}]
    _db_ms._ms_cache_ts = 0.0  # force expiry so get_media_servers re-queries

    class _BoomCtx:
        async def __aenter__(self):
            raise RuntimeError("db unavailable")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(_db_ms, "get_db", lambda: _BoomCtx())
    result = await _db_ms.get_media_servers()
    assert result == [{"id": "0", "type": "emby"}]


# ─── _invalidate_media_servers_cache ───────────────────────────────────────────

async def test_invalidate_media_servers_cache_forces_next_read_to_hit_db():
    await _db_ms.save_media_servers([{"id": "0", "type": "emby"}])
    assert _db_ms._ms_cache_ts > 0.0

    _db_ms._invalidate_media_servers_cache()
    assert _db_ms._ms_cache_ts == 0.0

    # Underlying data unchanged but cache is fresh again after next read
    result = await _db_ms.get_media_servers()
    assert result == [{"id": "0", "type": "emby"}]


# ─── server_type / is_plex / is_emby_compatible ────────────────────────────────

@pytest.mark.parametrize("server,expected", [
    ({"type": "Plex"}, "plex"),
    ({"type": "EMBY"}, "emby"),
    ({}, ""),
    ({"type": None}, ""),
])
def test_server_type_normalizes_case_and_handles_missing(server, expected):
    assert _db_ms.server_type(server) == expected


def test_is_plex_true_for_plex_server():
    assert _db_ms.is_plex({"type": "plex"}) is True


def test_is_plex_false_for_emby_server():
    assert _db_ms.is_plex({"type": "emby"}) is False


@pytest.mark.parametrize("server_type_value", ["emby", "jellyfin", ""])
def test_is_emby_compatible_true_for_emby_jellyfin_and_legacy_unset(server_type_value):
    assert _db_ms.is_emby_compatible({"type": server_type_value}) is True


def test_is_emby_compatible_false_for_plex():
    assert _db_ms.is_emby_compatible({"type": "plex"}) is False
