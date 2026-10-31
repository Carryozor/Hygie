"""Security hardening: logout by cookie, client IP extraction, rate-limit keys."""
import os
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")
os.environ.pop("DATABASE_URL", None)

COOKIE = "hygie_refresh"
PWD = "supersecret123"


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
    await auth_mod.create_user("alice", PWD)

    from backend.routers import auth as auth_router
    app = FastAPI()
    app.include_router(auth_router.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    _eng.SQLITE_PATH = orig_engine
    _db_utils.DB_PATH = orig_utils
    auth_mod._rate_buckets.clear()


async def _login(c, user="alice", pwd=PWD):
    return await c.post("/api/auth/login", json={"username": user, "password": pwd})


# ─── S1 : logout authenticated by the refresh cookie only ────────────────────
@pytest.mark.asyncio
async def test_logout_without_access_token_revokes_cookie_token(auth_client):
    r = await _login(auth_client)
    assert r.status_code == 200
    raw = auth_client.cookies.get(COOKIE)
    assert raw

    # No Authorization header (access token expired) — only the cookie.
    out = await auth_client.post("/api/auth/logout", json={})
    assert out.status_code == 200
    set_cookie = out.headers.get("set-cookie", "").lower()
    assert COOKIE in set_cookie and "max-age=0" in set_cookie
    assert "path=/api/auth" in set_cookie
    assert "samesite=strict" in set_cookie

    # The token itself must be dead server-side (effect, not just cookie).
    auth_client.cookies.clear()
    again = await auth_client.post("/api/auth/refresh", json={"refresh_token": raw})
    assert again.status_code == 401


@pytest.mark.asyncio
async def test_logout_with_nothing_is_idempotent_200(auth_client):
    out = await auth_client.post("/api/auth/logout", json={})
    assert out.status_code == 200
    assert COOKIE in out.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_logout_with_unknown_token_still_clears_cookie(auth_client):
    out = await auth_client.post("/api/auth/logout", json={"refresh_token": "bogus"})
    assert out.status_code == 200
    assert "max-age=0" in out.headers.get("set-cookie", "").lower()


# ─── S3 : client IP ──────────────────────────────────────────────────────────
def _req(headers, host="172.27.0.9"):
    r = MagicMock()
    r.headers = headers
    r.client = MagicMock(host=host)
    return r


def test_forged_xff_uses_rightmost_entry_with_trusted_proxy(monkeypatch):
    import backend.auth as a
    monkeypatch.setattr(a, "_TRUST_PROXY", True)
    assert a.get_client_ip(_req({"X-Forwarded-For": "1.2.3.4, 9.9.9.9"})) == "9.9.9.9"


def test_cf_connecting_ip_preferred_when_trusted(monkeypatch):
    import backend.auth as a
    monkeypatch.setattr(a, "_TRUST_PROXY", True)
    h = {"CF-Connecting-IP": "8.8.4.4", "X-Forwarded-For": "1.2.3.4, 9.9.9.9"}
    assert a.get_client_ip(_req(h)) == "8.8.4.4"


def test_cf_connecting_ip_ignored_when_untrusted(monkeypatch):
    import backend.auth as a
    monkeypatch.setattr(a, "_TRUST_PROXY", False)
    assert a.get_client_ip(_req({"CF-Connecting-IP": "8.8.4.4"})) == "172.27.0.9"


def test_invalid_cf_header_falls_back_to_xff(monkeypatch):
    import backend.auth as a
    monkeypatch.setattr(a, "_TRUST_PROXY", True)
    h = {"CF-Connecting-IP": "not-an-ip", "X-Forwarded-For": "1.2.3.4, 9.9.9.9"}
    assert a.get_client_ip(_req(h)) == "9.9.9.9"


# ─── S3 : rate-limit budgets ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_refresh_without_cookie_does_not_consume_budget(auth_client):
    for _ in range(8):
        r = await auth_client.post("/api/auth/refresh", json={})
        assert r.status_code == 401  # never 429
    ok = await _login(auth_client)
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_login_budget_is_per_ip_and_username(auth_client):
    for _ in range(5):
        r = await _login(auth_client, "alice", "wrongpassword")
        assert r.status_code == 401
    assert (await _login(auth_client, "alice", "wrongpassword")).status_code == 429
    # Another username from the same IP keeps its own budget.
    assert (await _login(auth_client, "mallory", "wrongpassword")).status_code == 401


@pytest.mark.asyncio
async def test_change_password_is_rate_limited(auth_client):
    tok = (await _login(auth_client)).json()["access_token"]
    hdr = {"Authorization": f"Bearer {tok}"}
    body = {"current_password": "wrongwrong", "new_password": "newpassword123"}
    for _ in range(5):
        assert (await auth_client.post("/api/auth/change-password", json=body, headers=hdr)).status_code == 401
    assert (await auth_client.post("/api/auth/change-password", json=body, headers=hdr)).status_code == 429


@pytest.mark.asyncio
async def test_change_password_success_releases_budget(auth_client):
    tok = (await _login(auth_client)).json()["access_token"]
    hdr = {"Authorization": f"Bearer {tok}"}
    bad = {"current_password": "wrongwrong", "new_password": "newpassword123"}
    for _ in range(3):
        await auth_client.post("/api/auth/change-password", json=bad, headers=hdr)
    good = {"current_password": PWD, "new_password": "newpassword123"}
    assert (await auth_client.post("/api/auth/change-password", json=good, headers=hdr)).status_code == 200
