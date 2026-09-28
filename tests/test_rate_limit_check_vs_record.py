"""Rate limiting must only lock out FAILURES, not every call.

Regression: rate_limit() previously counted every call (success or failure).
A legitimate user making 6 successful calls within the window (page reloads
triggering /refresh, Plex sending play/pause/resume/stop/scrobble, repeated
public dashboard visits) got locked out on the 6th call.

Fix: split into is_rate_limited(key) -> bool (check only, never records) and
record_failure(key) -> None (records only, always called on the failure path).
setup keeps the old count-every-call behaviour via rate_limit() (unchanged).
"""
import os
import time
import pytest
import pytest_asyncio

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")


# ─── Unit level: auth.is_rate_limited / auth.record_failure ──────────────────

@pytest.fixture
def clean_auth():
    import backend.auth as auth_mod
    auth_mod.DB_PATH = ":memory:"
    auth_mod._rate_buckets.clear()
    yield auth_mod
    auth_mod._rate_buckets.clear()


def test_is_rate_limited_does_not_record(clean_auth):
    """Calling is_rate_limited() repeatedly must never itself trip the limiter."""
    key = f"check-only-{time.time()}"
    for _ in range(50):
        assert clean_auth.is_rate_limited(key) is False


def test_record_failure_then_is_rate_limited_blocks_after_max(clean_auth):
    key = f"record-{time.time()}"
    for _ in range(clean_auth.RATE_LIMIT_MAX):
        assert clean_auth.is_rate_limited(key) is False
        clean_auth.record_failure(key)
    assert clean_auth.is_rate_limited(key) is True


def test_successful_calls_never_lock_out(clean_auth):
    """10 successful calls (check only, no record_failure) must never be blocked."""
    key = f"success-only-{time.time()}"
    for _ in range(10):
        assert clean_auth.is_rate_limited(key) is False


# ─── Integration: /api/auth/login ─────────────────────────────────────────────

@pytest_asyncio.fixture
async def auth_client(tmp_path):
    """Minimal app with only the auth router — real sqlite-backed rate limiting."""
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    from argon2 import PasswordHasher
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _ss

    import backend.auth as _auth_mod
    db_path = str(tmp_path / "auth_rl.db")
    _auth_mod.DB_PATH = db_path
    _auth_mod._rate_buckets.clear()
    _auth_mod._ph = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)

    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0

    from backend.db.schema import init_db
    await init_db()

    from backend.routers import auth as auth_router
    app = FastAPI()
    app.include_router(auth_router.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/api/auth/setup", json={"username": "admin", "password": "strongpass123"})
        yield c

    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    _auth_mod._rate_buckets.clear()


@pytest.mark.asyncio
async def test_ten_successful_logins_never_429(auth_client):
    for _ in range(10):
        resp = await auth_client.post(
            "/api/auth/login", json={"username": "admin", "password": "strongpass123"}
        )
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_six_failed_logins_trigger_429(auth_client):
    from backend.auth import RATE_LIMIT_MAX
    last = None
    for _ in range(RATE_LIMIT_MAX + 1):
        last = await auth_client.post(
            "/api/auth/login", json={"username": "admin", "password": "wrongpassword"}
        )
    assert last.status_code == 429


@pytest.mark.asyncio
async def test_lockout_blocks_even_correct_credentials(auth_client):
    from backend.auth import RATE_LIMIT_MAX
    for _ in range(RATE_LIMIT_MAX + 1):
        await auth_client.post(
            "/api/auth/login", json={"username": "admin", "password": "wrongpassword"}
        )
    resp = await auth_client.post(
        "/api/auth/login", json={"username": "admin", "password": "strongpass123"}
    )
    assert resp.status_code == 429


# ─── Integration: /api/auth/refresh ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_ten_successful_refreshes_never_429(auth_client):
    for _ in range(10):
        resp = await auth_client.post("/api/auth/refresh", json={})
        assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_six_failed_refreshes_trigger_429(auth_client):
    """setup() (called by the fixture) already left a valid refresh cookie on
    the client — clear it first so these calls actually fail (cookie takes
    priority over the body field in the /refresh route)."""
    from backend.auth import RATE_LIMIT_MAX
    auth_client.cookies.clear()
    last = None
    for _ in range(RATE_LIMIT_MAX + 1):
        last = await auth_client.post(
            "/api/auth/refresh", json={"refresh_token": "not-a-real-token"}
        )
    assert last.status_code == 429


@pytest.mark.asyncio
async def test_refresh_lockout_blocks_even_valid_token(auth_client):
    from backend.auth import RATE_LIMIT_MAX
    # Obtain a genuinely valid refresh cookie first (from setup in the fixture).
    ok = await auth_client.post("/api/auth/refresh", json={})
    assert ok.status_code == 200
    valid_cookie = dict(auth_client.cookies)

    auth_client.cookies.clear()
    for _ in range(RATE_LIMIT_MAX + 1):
        await auth_client.post("/api/auth/refresh", json={"refresh_token": "still-not-real"})

    # Restore the valid cookie — even a genuinely valid token must now be blocked.
    auth_client.cookies.update(valid_cookie)
    resp = await auth_client.post("/api/auth/refresh", json={})
    assert resp.status_code == 429


# ─── Integration: Plex webhook ────────────────────────────────────────────────

@pytest_asyncio.fixture
async def webhook_client(tmp_path):
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _ss

    import backend.auth as _auth_mod
    _auth_mod.DB_PATH = ":memory:"
    _auth_mod._rate_buckets.clear()

    db_path = str(tmp_path / "wh_rl.db")
    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0

    from backend.db.schema import init_db
    await init_db()
    from backend.db.settings_store import set_setting
    await set_setting("plex_webhook_secret", "s3cret-token")

    from backend.routers import plex_webhook
    app = FastAPI()
    app.include_router(plex_webhook.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    _auth_mod._rate_buckets.clear()


def _scrobble_payload() -> str:
    import json
    return json.dumps({
        "event": "media.scrobble",
        "Account": {"id": 1, "title": "testuser"},
        "Metadata": {"ratingKey": "101", "title": "Inception", "lastViewedAt": 1700000000},
    })


@pytest.mark.asyncio
async def test_ten_correct_secret_webhook_calls_never_429(webhook_client):
    for _ in range(10):
        resp = await webhook_client.post(
            "/api/plex/webhook?secret=s3cret-token", data={"payload": _scrobble_payload()}
        )
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_webhook_lockout_blocks_even_correct_secret(webhook_client):
    from backend.auth import RATE_LIMIT_MAX
    for _ in range(RATE_LIMIT_MAX + 1):
        await webhook_client.post(
            "/api/plex/webhook?secret=wrong", data={"payload": _scrobble_payload()}
        )
    resp = await webhook_client.post(
        "/api/plex/webhook?secret=s3cret-token", data={"payload": _scrobble_payload()}
    )
    assert resp.status_code == 429


# ─── Integration: public dashboard ────────────────────────────────────────────

@pytest_asyncio.fixture
async def public_client(tmp_path):
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _ss

    import backend.auth as _auth_mod
    _auth_mod.DB_PATH = ":memory:"
    _auth_mod._rate_buckets.clear()

    db_path = str(tmp_path / "public_rl.db")
    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0

    from backend.db.schema import init_db
    await init_db()
    from backend.db.settings_store import set_setting
    await set_setting("public_dashboard_enabled", "true")
    await set_setting("public_dashboard_slug", "myslug")

    from backend.routers import public as public_router
    app = FastAPI()
    app.include_router(public_router.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    _auth_mod._rate_buckets.clear()


@pytest.mark.asyncio
async def test_ten_successful_public_dashboard_calls_never_429(public_client):
    for _ in range(10):
        resp = await public_client.get("/api/public/upcoming", params={"slug": "myslug"})
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_public_dashboard_lockout_blocks_even_correct_slug(public_client):
    from backend.auth import RATE_LIMIT_MAX
    for _ in range(RATE_LIMIT_MAX + 1):
        await public_client.get("/api/public/upcoming", params={"slug": "wrong"})
    resp = await public_client.get("/api/public/upcoming", params={"slug": "myslug"})
    assert resp.status_code == 429
