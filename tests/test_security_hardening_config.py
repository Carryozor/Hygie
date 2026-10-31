"""Security hardening: sensitive settings, public endpoint filtering, openapi, bounds."""
import os

import pytest
import pytest_asyncio

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")


def test_sensitive_keys_cover_dashboard_password_and_prometheus_token():
    from backend.db.encryption import SENSITIVE_KEYS
    assert "public_dashboard_password" in SENSITIVE_KEYS
    assert "prometheus_bearer_token" in SENSITIVE_KEYS


def test_settings_update_accepts_prometheus_bearer_token():
    from backend.routers.settings import SettingsUpdate
    assert SettingsUpdate(prometheus_bearer_token="abc").model_dump(exclude_none=True) == {
        "prometheus_bearer_token": "abc"}


def test_openapi_schema_not_served():
    from backend.main import app
    assert app.openapi_url is None


@pytest_asyncio.fixture
async def env(tmp_path):
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _ss
    import backend.db.media_servers as _db_ms
    import backend.auth as _auth

    _auth.DB_PATH = ":memory:"
    _auth._rate_buckets.clear()
    db_path = str(tmp_path / "cfg.db")
    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    _db_ms._ms_cache = None
    from backend.db.schema import init_db
    await init_db()
    from backend.db.settings_store import set_setting
    await set_setting("public_dashboard_enabled", "true")

    from backend.routers import public as public_router
    from backend.routers import settings as settings_router
    app = FastAPI()
    app.state.scheduler = None
    app.include_router(public_router.router)
    app.include_router(settings_router.router)
    app.dependency_overrides[settings_router.require_auth] = lambda: "u"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    _auth._rate_buckets.clear()


def _soon():
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()


async def _seed_item(poster, username="jean"):
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_name, library_id, "
            "delete_at, poster_url, seerr_username, status, file_path, detected_at) "
            "VALUES ('e1','T','movie','L','lib1',?,?,?, 'pending', '/x', '2026-01-01T00:00:00+00:00')",
            (_soon(), poster, username),
        )
        await db.commit()


def _first(resp):
    events = resp.json()["events"]
    return next(iter(events.values()))[0]


@pytest.mark.asyncio
async def test_public_strips_plex_token_from_poster_url(env):
    await _seed_item("http://plex:32400/library/metadata/1/thumb?X-Plex-Token=SECRETTOK&w=300")
    item = _first(await env.get("/api/public/upcoming"))
    assert "SECRETTOK" not in item["poster_url"]
    assert "X-Plex-Token" not in item["poster_url"]
    assert "w=300" in item["poster_url"]


@pytest.mark.asyncio
async def test_public_hides_username_when_unprotected(env):
    await _seed_item("http://x/p.jpg")
    assert _first(await env.get("/api/public/upcoming"))["seerr_username"] in ("", None)


@pytest.mark.asyncio
async def test_public_shows_username_when_slug_protected(env):
    from backend.db.settings_store import set_setting
    await set_setting("public_dashboard_slug", "s3cret-slug")
    await _seed_item("http://x/p.jpg")
    item = _first(await env.get("/api/public/upcoming?slug=s3cret-slug"))
    assert item["seerr_username"] == "jean"


@pytest.mark.asyncio
async def test_public_shows_username_when_password_protected(env):
    from backend.db.settings_store import set_setting
    await set_setting("public_dashboard_password", "pw")
    await _seed_item("http://x/p.jpg")
    r = await env.get("/api/public/upcoming", headers={"X-Dashboard-Password": "pw"})
    assert _first(r)["seerr_username"] == "jean"


@pytest.mark.asyncio
async def test_dashboard_password_masked_and_encrypted_at_rest(env):
    r = await env.post("/api/settings", json={
        "public_dashboard_password": "hunter2hunter2", "prometheus_bearer_token": "tok-123456"})
    assert r.status_code == 200
    got = (await env.get("/api/settings")).json()
    assert got["public_dashboard_password"] == "***"
    assert got["prometheus_bearer_token"] == "***"
    from backend.db.engine import get_db
    async with get_db() as db:
        row = await db.fetch_one("SELECT value FROM settings WHERE `key`=?", ("public_dashboard_password",))
    assert row["value"].startswith("enc:") and "hunter2" not in row["value"]
    # Readers still get the plaintext through the decrypting API.
    from backend.db.settings_store import get_setting
    assert await get_setting("public_dashboard_password") == "hunter2hunter2"
    # Public endpoint still validates against the decrypted value.
    ok = await env.get("/api/public/upcoming", headers={"X-Dashboard-Password": "hunter2hunter2"})
    assert ok.status_code == 200


# ─── S6 : grace_days bounds ──────────────────────────────────────────────────
@pytest.mark.parametrize("bad", [-1, 3651, 10**9])
def test_seerr_rule_grace_days_bounded(bad):
    from pydantic import ValidationError
    from backend.routers.seerr_rules import RuleBody
    with pytest.raises(ValidationError):
        RuleBody(seerr_user_id=1, seerr_username="u", grace_days=bad)


@pytest.mark.parametrize("ok", [0, 30, 3650])
def test_seerr_rule_grace_days_accepts_valid_range(ok):
    from backend.routers.seerr_rules import RuleBody
    assert RuleBody(seerr_user_id=1, seerr_username="u", grace_days=ok).grace_days == ok
