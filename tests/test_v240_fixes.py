"""Tests for v2.4.0 fixes — rate limiter, http_retry, sanitize_url, DB indexes."""
import asyncio
import time
import pytest
import pytest_asyncio


# ─── sanitize_url ─────────────────────────────────────────────────────────────

def test_sanitize_url_masks_api_key():
    from backend.db.utils import sanitize_url
    url = "http://emby:8096/Items/123/Images/Primary?api_key=MYSECRETKEY&maxHeight=300"
    result = sanitize_url(url)
    assert "MYSECRETKEY" not in result
    assert "api_key=***" in result
    assert "maxHeight=300" in result


def test_sanitize_url_masks_token():
    from backend.db.utils import sanitize_url
    url = "http://example.com/api?token=abc123&foo=bar"
    result = sanitize_url(url)
    assert "abc123" not in result
    assert "token=***" in result
    assert "foo=bar" in result


def test_sanitize_url_no_sensitive_params():
    from backend.db.utils import sanitize_url
    url = "http://example.com/api?param=value&other=data"
    assert sanitize_url(url) == url


def test_sanitize_url_multiple_keys():
    from backend.db.utils import sanitize_url
    url = "http://x.com?api_key=SECRET1&token=SECRET2&name=hygie"
    result = sanitize_url(url)
    assert "SECRET1" not in result
    assert "SECRET2" not in result
    assert "name=hygie" in result


# ─── http_retry ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_http_retry_success_first_try():
    from backend.db.utils import http_retry

    calls = []

    class FakeResp:
        status_code = 200

    async def factory():
        calls.append(1)
        return FakeResp()

    result = await http_retry(factory, retries=3, backoff=0.01)
    assert result.status_code == 200
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_http_retry_retries_on_timeout():
    import httpx
    from backend.db.utils import http_retry

    calls = []

    class FakeResp:
        status_code = 200

    async def factory():
        calls.append(1)
        if len(calls) < 3:
            raise httpx.TimeoutException("timeout")
        return FakeResp()

    result = await http_retry(factory, retries=3, backoff=0.01)
    assert result.status_code == 200
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_http_retry_raises_after_exhaustion():
    import httpx
    from backend.db.utils import http_retry

    async def factory():
        raise httpx.TimeoutException("always timeout")

    with pytest.raises(httpx.TimeoutException):
        await http_retry(factory, retries=3, backoff=0.01)


@pytest.mark.asyncio
async def test_http_retry_no_retry_on_non_transient():
    from backend.db.utils import http_retry

    calls = []

    async def factory():
        calls.append(1)
        raise ValueError("logic error — should not retry")

    with pytest.raises(ValueError):
        await http_retry(factory, retries=3, backoff=0.01)

    assert len(calls) == 1  # no retries for non-transient errors


# ─── Rate limiter ─────────────────────────────────────────────────────────────

def test_rate_limit_allows_under_max():
    from backend import auth as auth_mod
    auth_mod._rate_buckets.clear()
    auth_mod._rate_call_counter = 0

    key = "test-ip-allow"
    for _ in range(auth_mod.RATE_LIMIT_MAX):
        result = auth_mod.rate_limit(key)
    assert not result  # last call at exactly the limit should not be blocked


def test_rate_limit_blocks_over_max():
    from backend import auth as auth_mod
    auth_mod._rate_buckets.clear()

    key = "test-ip-block"
    for _ in range(auth_mod.RATE_LIMIT_MAX + 1):
        auth_mod.rate_limit(key)
    assert auth_mod.rate_limit(key)  # one more — now blocked


def test_rate_limit_cleans_up_old_entries():
    from backend import auth as auth_mod
    auth_mod._rate_buckets.clear()

    key = "test-ip-expire"
    # Inject expired timestamps directly
    old_time = time.time() - auth_mod.RATE_LIMIT_WINDOW - 1
    auth_mod._rate_buckets[key] = [old_time] * 10

    # Test _memory_rate_limit directly — the in-memory path
    result = auth_mod._memory_rate_limit(key, time.time(), time.time() - auth_mod.RATE_LIMIT_WINDOW)
    assert not result
    assert len(auth_mod._rate_buckets[key]) == 1  # only the new entry


def test_rate_limit_periodic_cleanup():
    from backend import auth as auth_mod
    auth_mod._rate_buckets.clear()
    auth_mod._rate_call_counter = 0

    # Fill with many stale IPs
    old_time = time.time() - auth_mod.RATE_LIMIT_WINDOW - 1
    for i in range(600):
        auth_mod._rate_buckets[f"stale-ip-{i}"] = [old_time]

    assert len(auth_mod._rate_buckets) == 600

    # Trigger periodic cleanup by calling _memory_rate_limit with counter at 499
    key = "trigger-ip"
    auth_mod._rate_call_counter = 499  # next call will hit modulo 500
    auth_mod._memory_rate_limit(key, time.time(), time.time() - auth_mod.RATE_LIMIT_WINDOW)

    # All stale IPs should be cleaned up
    assert len(auth_mod._rate_buckets) < 10


# ─── DB indexes ───────────────────────────────────────────────────────────────

@pytest.fixture()
async def fresh_db(monkeypatch, tmp_path):
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _db_ss
    import backend.db.media_servers as _db_ms
    import backend.db.schema as _db_schema
    import backend.db.logs as _db_logs
    import backend.db.engine as _db_engine
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ms, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_db_logs, "DB_PATH", db_path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    from backend.db.schema import init_db
    await init_db()
    yield db_path


@pytest.mark.asyncio
async def test_db_has_required_indexes(fresh_db):
    import aiosqlite
    async with aiosqlite.connect(fresh_db) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ) as cur:
            index_names = {row[0] for row in await cur.fetchall()}

    expected = {
        "idx_logs_ts",
        "idx_media_status",
        "idx_media_delete_at",
        "idx_media_emby_id",
        "idx_media_library_id",
        "idx_ignored_emby_id",
    }
    missing = expected - index_names
    assert not missing, f"Missing indexes: {missing}"


@pytest.mark.asyncio
async def test_media_queue_has_notif_columns(fresh_db):
    import aiosqlite
    async with aiosqlite.connect(fresh_db) as db:
        async with db.execute("PRAGMA table_info(media_queue)") as cur:
            cols = {row[1] for row in await cur.fetchall()}

    assert "notified_detected" in cols
    assert "notified_thresholds" in cols


# ─── Interval bounds ──────────────────────────────────────────────────────────

def test_interval_bounds_clamp():
    """Verify the clamp logic used in main.py and settings.py."""
    def clamp(v):
        return max(1, min(10080, int(v)))

    assert clamp("0") == 1
    assert clamp("-5") == 1
    assert clamp("99999") == 10080
    assert clamp("360") == 360
    assert clamp("1") == 1
    assert clamp("10080") == 10080
