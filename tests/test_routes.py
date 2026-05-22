"""Smoke tests for FastAPI routes using TestClient with isolated DB."""
import os
import pytest
from httpx import AsyncClient, ASGITransport

# Env vars must be set before any backend import
os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")


@pytest.fixture()
async def client(tmp_path, monkeypatch):
    """Full FastAPI app with isolated per-test DB."""
    import backend.database as dbmod

    db_path = str(tmp_path / "route_test.db")
    monkeypatch.setattr(dbmod, "DB_PATH", db_path)
    dbmod._ms_cache = None
    dbmod._ms_cache_ts = 0.0
    dbmod._settings_cache.clear()
    dbmod._settings_cache_ts = 0.0

    # Import AFTER patching so lifespan sees the right DB_PATH
    import importlib
    import backend.auth as auth_mod
    import backend.main as main_mod
    importlib.reload(auth_mod)   # re-load to pick up fresh SECRET_FILE path
    importlib.reload(main_mod)

    app = main_mod.app
    async with main_mod.lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.fixture()
async def registered_client(client):
    """Client with a registered user; returns (client, token)."""
    r = await client.post("/api/auth/setup",
                          json={"username": "admin", "password": "strongpass123"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    return client, token


# ─── public endpoints ─────────────────────────────────────────────────────────

async def test_health_returns_healthy(client):
    r = await client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] in ("healthy", "degraded")
    assert "version" in data
    assert "database" in data
    assert "scheduler" in data


async def test_version_returns_version_string(client):
    r = await client.get("/api/version")
    assert r.status_code == 200
    assert "version" in r.json()
    assert isinstance(r.json()["version"], str)


@pytest.mark.skipif(
    __import__("sys").version_info >= (3, 13),
    reason="Jinja2 LRU cache incompatibility with Python 3.13 in test env; passes in container (3.12)"
)
async def test_index_returns_html(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


# ─── auth — setup flow ─────────────────────────────────────────────────────────

async def test_status_before_setup_returns_false(client):
    r = await client.get("/api/auth/status")
    assert r.status_code == 200
    assert r.json()["setup_complete"] is False


async def test_setup_creates_user_and_returns_token(client):
    r = await client.post("/api/auth/setup",
                          json={"username": "admin", "password": "strongpass123"})
    assert r.status_code == 200
    data = r.json()
    assert "token" in data
    assert data["username"] == "admin"


async def test_setup_blocked_when_user_already_exists(registered_client):
    c, _ = registered_client
    r = await c.post("/api/auth/setup",
                     json={"username": "other", "password": "anotherpass123"})
    assert r.status_code == 409


async def test_status_after_setup_returns_true(registered_client):
    c, _ = registered_client
    r = await c.get("/api/auth/status")
    assert r.json()["setup_complete"] is True


# ─── auth — login ─────────────────────────────────────────────────────────────

async def test_login_returns_token(registered_client):
    c, _ = registered_client
    r = await c.post("/api/auth/login",
                     json={"username": "admin", "password": "strongpass123"})
    assert r.status_code == 200
    assert "token" in r.json()


async def test_login_wrong_password_returns_401(registered_client):
    c, _ = registered_client
    r = await c.post("/api/auth/login",
                     json={"username": "admin", "password": "wrongpassword"})
    assert r.status_code == 401


async def test_login_unknown_user_returns_401(registered_client):
    c, _ = registered_client
    r = await c.post("/api/auth/login",
                     json={"username": "nobody", "password": "somepassword"})
    assert r.status_code == 401


# ─── auth — protected routes ──────────────────────────────────────────────────

async def test_settings_requires_auth(client):
    r = await client.get("/api/settings")
    assert r.status_code == 401


async def test_settings_accessible_with_token(registered_client):
    c, token = registered_client
    r = await c.get("/api/settings", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


async def test_invalid_token_returns_401(registered_client):
    c, _ = registered_client
    r = await c.get("/api/settings", headers={"Authorization": "Bearer invalid.token.here"})
    assert r.status_code == 401


async def test_me_returns_username(registered_client):
    c, token = registered_client
    r = await c.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["username"] == "admin"


# ─── auth — password change ───────────────────────────────────────────────────

async def test_change_password_with_correct_current(registered_client):
    c, token = registered_client
    r = await c.post("/api/auth/change-password",
                     json={"current_password": "strongpass123", "new_password": "newstrongpass456"},
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200

    # Can login with new password
    r2 = await c.post("/api/auth/login",
                      json={"username": "admin", "password": "newstrongpass456"})
    assert r2.status_code == 200


async def test_change_password_wrong_current_returns_401(registered_client):
    c, token = registered_client
    r = await c.post("/api/auth/change-password",
                     json={"current_password": "wrongpassword", "new_password": "newpass123456"},
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


# ─── image proxy security ─────────────────────────────────────────────────────

async def test_proxy_rejects_unlisted_host(client):
    import urllib.parse
    url = urllib.parse.quote("http://evil.example.com/image.jpg", safe="")
    r = await client.get(f"/api/proxy/image?url={url}")
    assert r.status_code == 403


async def test_proxy_rejects_file_scheme(client):
    import urllib.parse
    url = urllib.parse.quote("file:///etc/passwd", safe="")
    r = await client.get(f"/api/proxy/image?url={url}")
    assert r.status_code in (400, 403)


async def test_proxy_rejects_missing_url_param(client):
    r = await client.get("/api/proxy/image")
    assert r.status_code == 400


async def test_proxy_rejects_ftp_scheme(client):
    import urllib.parse
    url = urllib.parse.quote("ftp://files.example.com/image.jpg", safe="")
    r = await client.get(f"/api/proxy/image?url={url}")
    assert r.status_code in (400, 403)


# ─── scheduler endpoints ──────────────────────────────────────────────────────

async def test_scheduler_status_requires_auth(client):
    r = await client.get("/api/scheduler/status")
    assert r.status_code == 401


async def test_scheduler_status_returns_jobs(registered_client):
    c, token = registered_client
    r = await c.get("/api/scheduler/status", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    job_ids = {j["id"] for j in r.json()}
    assert "scan_job" in job_ids
    assert "deletion_job" in job_ids


async def test_run_unknown_job_returns_404(registered_client):
    c, token = registered_client
    r = await c.post("/api/scheduler/run/nonexistent_job",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404


# ─── global stats ─────────────────────────────────────────────────────────────

async def test_global_stats_requires_auth(client):
    r = await client.get("/api/stats/global")
    assert r.status_code == 401


async def test_global_stats_returns_expected_fields(registered_client):
    c, token = registered_client
    r = await c.get("/api/stats/global", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert "total_deleted" in data
    assert "total_ignored" in data
    assert "total_scans" in data
    assert "queue" in data
    assert "by_month" in data
