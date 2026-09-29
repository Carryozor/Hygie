"""Coverage tests for backend/routers/auth.py — the 5 lines existing tests
never exercised: rate-limited 429 on /setup, /login and /refresh, and
POST /logout-all revoking every refresh token for the user.

Standalone app (same pattern as tests/test_auth_cookie.py) — real auth flow,
no dependency override, since this file's whole point is testing the auth
gate itself.
"""
from unittest.mock import patch

import pytest_asyncio
import os

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")
os.environ.pop("DATABASE_URL", None)


@pytest_asyncio.fixture
async def auth_client(tmp_path):
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.auth as auth_mod

    db_path = str(tmp_path / "auth_cov.db")
    orig_engine, orig_utils = _eng.SQLITE_PATH, _db_utils.DB_PATH
    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    auth_mod._rate_buckets.clear()

    from backend.db.schema import init_db
    await init_db()

    from backend.routers import auth as auth_router
    app = FastAPI()
    app.include_router(auth_router.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    _eng.SQLITE_PATH = orig_engine
    _db_utils.DB_PATH = orig_utils
    auth_mod._rate_buckets.clear()


async def test_setup_returns_429_when_rate_limited(auth_client):
    with patch("backend.routers.auth.rate_limit", return_value=True):
        r = await auth_client.post(
            "/api/auth/setup", json={"username": "admin", "password": "strongpass123"}
        )
    assert r.status_code == 429


async def test_login_returns_429_when_rate_limited(auth_client):
    with patch("backend.routers.auth.rate_limit_attempt", return_value=(True, "tok")):
        r = await auth_client.post(
            "/api/auth/login", json={"username": "admin", "password": "whatever1"}
        )
    assert r.status_code == 429


async def test_refresh_returns_429_when_rate_limited(auth_client):
    with patch("backend.routers.auth.rate_limit_attempt", return_value=(True, "tok")):
        r = await auth_client.post("/api/auth/refresh", json={"refresh_token": "whatever"})
    assert r.status_code == 429


async def test_logout_all_revokes_every_refresh_token(auth_client):
    await auth_client.post(
        "/api/auth/setup", json={"username": "bob", "password": "strongpass123"}
    )
    login1 = (await auth_client.post(
        "/api/auth/login", json={"username": "bob", "password": "strongpass123"}
    )).json()
    raw1 = auth_client.cookies.get("hygie_refresh")
    auth_client.cookies.clear()

    login2 = (await auth_client.post(
        "/api/auth/login", json={"username": "bob", "password": "strongpass123"}
    )).json()
    raw2 = auth_client.cookies.get("hygie_refresh")
    auth_client.cookies.clear()

    r = await auth_client.post(
        "/api/auth/logout-all",
        headers={"Authorization": f"Bearer {login2['access_token']}"},
    )
    assert r.status_code == 200
    assert r.json() == {"status": "all_sessions_revoked"}

    # Both sessions' refresh tokens must now be dead
    r1 = await auth_client.post("/api/auth/refresh", json={"refresh_token": raw1})
    assert r1.status_code == 401
    r2 = await auth_client.post("/api/auth/refresh", json={"refresh_token": raw2})
    assert r2.status_code == 401
