"""Tests: refresh token delivered via httpOnly cookie.

The access token stays in memory/localStorage (short-lived, 1h) but the
30-day refresh token must not be reachable from JavaScript — it is set as
an httpOnly cookie scoped to /api/auth. The JSON body field is kept one
release for backward compatibility.
"""
import os
import pytest
import pytest_asyncio

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")
os.environ.pop("DATABASE_URL", None)

COOKIE = "hygie_refresh"


@pytest_asyncio.fixture
async def auth_client(tmp_path):
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.auth as auth_mod

    db_path = str(tmp_path / "auth.db")
    orig_engine, orig_utils = _eng.SQLITE_PATH, _db_utils.DB_PATH
    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    auth_mod._rate_buckets.clear()

    from backend.db.schema import init_db
    await init_db()
    await auth_mod.create_user("alice", "supersecret123")

    from backend.routers import auth as auth_router
    app = FastAPI()
    app.include_router(auth_router.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    _eng.SQLITE_PATH = orig_engine
    _db_utils.DB_PATH = orig_utils
    auth_mod._rate_buckets.clear()


async def _login(c):
    r = await c.post("/api/auth/login",
                     json={"username": "alice", "password": "supersecret123"})
    assert r.status_code == 200
    return r


@pytest.mark.asyncio
async def test_login_sets_httponly_refresh_cookie(auth_client):
    r = await _login(auth_client)
    set_cookie = r.headers.get("set-cookie", "")
    assert COOKIE in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "path=/api/auth" in set_cookie.lower()
    assert "samesite=strict" in set_cookie.lower()


@pytest.mark.asyncio
async def test_refresh_works_with_cookie_only(auth_client):
    await _login(auth_client)
    # httpx client persists cookies — empty body means "use the cookie"
    r = await auth_client.post("/api/auth/refresh", json={})
    assert r.status_code == 200
    assert r.json().get("access_token")


@pytest.mark.asyncio
async def test_refresh_still_accepts_body_token(auth_client):
    r = await _login(auth_client)
    raw = r.json()["refresh_token"]
    auth_client.cookies.clear()
    r2 = await auth_client.post("/api/auth/refresh", json={"refresh_token": raw})
    assert r2.status_code == 200
    assert r2.json().get("access_token")


@pytest.mark.asyncio
async def test_logout_revokes_and_clears_cookie(auth_client):
    r = await _login(auth_client)
    raw = r.json()["refresh_token"]
    access = r.json()["access_token"]

    r2 = await auth_client.post(
        "/api/auth/logout", json={},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r2.status_code == 200
    # Cookie cleared in the response
    set_cookie = r2.headers.get("set-cookie", "")
    assert COOKIE in set_cookie

    # The revoked token must no longer refresh (cookie or body)
    auth_client.cookies.clear()
    r3 = await auth_client.post("/api/auth/refresh", json={"refresh_token": raw})
    assert r3.status_code == 401


@pytest.mark.asyncio
async def test_refresh_without_cookie_or_body_is_401(auth_client):
    auth_client.cookies.clear()
    r = await auth_client.post("/api/auth/refresh", json={})
    assert r.status_code == 401
