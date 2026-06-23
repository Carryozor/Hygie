"""Tests for security/reliability hardening fixes.

Covers: SSRF proxy (port + redirect validation), Plex webhook fail-closed secret,
atomic deletion claim, dry-run status preservation, SQLite busy_timeout,
fetch_one heuristic, LIKE wildcard escaping.
"""
import os
import json
import httpx
import pytest
import pytest_asyncio
import respx

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")
os.environ.pop("DATABASE_URL", None)


# ─── Image proxy: scheme + host + port validation ─────────────────────────────

def test_proxy_url_allowed_checks_port():
    from backend.proxy import _is_url_allowed
    allowed = {("radarr.local", 7878)}
    assert _is_url_allowed("http://radarr.local:7878/img.jpg", allowed)
    assert not _is_url_allowed("http://radarr.local:9999/img.jpg", allowed)


def test_proxy_url_allowed_default_ports():
    from backend.proxy import _is_url_allowed
    allowed = {("image.tmdb.org", 443), ("image.tmdb.org", 80)}
    assert _is_url_allowed("https://image.tmdb.org/t/p/w500/x.jpg", allowed)
    assert _is_url_allowed("http://image.tmdb.org/t/p/w500/x.jpg", allowed)
    assert not _is_url_allowed("https://image.tmdb.org:8443/x.jpg", allowed)


def test_proxy_url_allowed_rejects_bad_scheme():
    from backend.proxy import _is_url_allowed
    allowed = {("image.tmdb.org", 443)}
    assert not _is_url_allowed("file:///etc/passwd", allowed)
    assert not _is_url_allowed("ftp://image.tmdb.org/x.jpg", allowed)


def test_proxy_url_allowed_rejects_unknown_host():
    from backend.proxy import _is_url_allowed
    allowed = {("image.tmdb.org", 443)}
    assert not _is_url_allowed("https://evil.example.com/x.jpg", allowed)


# ─── Image proxy: redirects must be re-validated per hop ──────────────────────

class _FakeRequest:
    def __init__(self, url: str):
        self.query_params = {"url": url}


class _FakeStream:
    def __init__(self, status_code: int, headers: dict):
        self.status_code = status_code
        self.headers = headers

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self, _size):
        yield b"\xff\xd8fakejpeg"


class _FakeAsyncClient:
    """Fake httpx.AsyncClient: good.example.com 302-redirects to evil.example.com."""
    requested: list = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, **kw):
        _FakeAsyncClient.requested.append(url)
        if "good.example.com" in url:
            return _FakeStream(302, {"location": "http://evil.example.com/secret.jpg"})
        return _FakeStream(200, {"content-type": "image/jpeg"})


@pytest.mark.asyncio
async def test_proxy_blocks_redirect_to_unlisted_host(monkeypatch):
    import backend.proxy as proxy_mod

    async def _fake_whitelist():
        return {("good.example.com", 80), ("good.example.com", 443)}

    monkeypatch.setattr(proxy_mod, "_get_proxy_whitelist", _fake_whitelist)
    monkeypatch.setattr(proxy_mod.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.requested = []

    resp = await proxy_mod.proxy_image(_FakeRequest("http://good.example.com/img.jpg"))

    assert resp.status_code != 200
    assert not any("evil.example.com" in u for u in _FakeAsyncClient.requested), (
        "proxy must NOT fetch a redirect target outside the whitelist"
    )


# ─── Plex webhook: fail-closed secret ─────────────────────────────────────────

@pytest_asyncio.fixture
async def wh_client(tmp_path):
    """Minimal app with only the webhook router — no scheduler, no lifespan."""
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _ss

    # Import backend.auth before any DB_PATH override below — auth.py does
    # `from .db.utils import DB_PATH` (a one-time snapshot), so if this is
    # the first-ever import of backend.auth in the process it must happen
    # while DB_PATH is still ":memory:", or rate_limit() silently switches
    # from the in-memory fallback to a real sqlite3 connection against
    # whatever stale path it captured.
    import backend.auth as _auth_mod
    _auth_mod.DB_PATH = ":memory:"  # belt-and-suspenders: force the memory-backed path regardless of import order
    _auth_mod._rate_buckets.clear()

    db_path = str(tmp_path / "wh.db")
    orig_engine, orig_utils = _eng.SQLITE_PATH, _db_utils.DB_PATH
    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0

    from backend.db.schema import init_db
    await init_db()

    from backend.routers import plex_webhook
    app = FastAPI()
    app.include_router(plex_webhook.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    _eng.SQLITE_PATH = orig_engine
    _db_utils.DB_PATH = orig_utils
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    _auth_mod._rate_buckets.clear()


def _scrobble_payload() -> str:
    return json.dumps({
        "event": "media.scrobble",
        "Account": {"id": 1, "title": "testuser"},
        "Metadata": {"ratingKey": "101", "title": "Inception", "lastViewedAt": 1700000000},
    })


@pytest.mark.asyncio
async def test_webhook_rejected_when_no_secret_configured(wh_client):
    resp = await wh_client.post(
        "/api/plex/webhook", data={"payload": _scrobble_payload()}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_webhook_rejected_with_wrong_secret(wh_client):
    from backend.db.settings_store import set_setting
    await set_setting("plex_webhook_secret", "s3cret-token")
    resp = await wh_client.post(
        "/api/plex/webhook?secret=wrong", data={"payload": _scrobble_payload()}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_webhook_accepted_with_correct_secret(wh_client):
    from backend.db.settings_store import set_setting
    await set_setting("plex_webhook_secret", "s3cret-token")
    resp = await wh_client.post(
        "/api/plex/webhook?secret=s3cret-token", data={"payload": _scrobble_payload()}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_webhook_rate_limited_after_repeated_wrong_secret(wh_client):
    """A network attacker can otherwise brute-force plex_webhook_secret with
    no rate limit at all — every other sensitive endpoint (login, refresh,
    setup) already calls rate_limit() before the secret comparison."""
    from backend.db.settings_store import set_setting
    from backend.auth import RATE_LIMIT_MAX
    await set_setting("plex_webhook_secret", "s3cret-token")
    for _ in range(RATE_LIMIT_MAX + 1):
        resp = await wh_client.post(
            "/api/plex/webhook?secret=wrong", data={"payload": _scrobble_payload()}
        )
    assert resp.status_code == 429


# ─── DB engine: busy_timeout + fetch_one heuristic ────────────────────────────

@pytest_asyncio.fixture
async def sqlite_db(tmp_path):
    import aiosqlite
    import backend.db.engine as eng
    assert eng.DIALECT == "sqlite"
    path = str(tmp_path / "engine.db")
    conn = await aiosqlite.connect(path)
    await conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, val TEXT)")
    await conn.commit()
    await conn.close()
    orig = eng.SQLITE_PATH
    eng.SQLITE_PATH = path
    yield
    eng.SQLITE_PATH = orig


@pytest.mark.asyncio
async def test_fetch_one_supports_pragma(sqlite_db):
    """fetch_one must not blindly append LIMIT 1 to non-SELECT statements."""
    from backend.db.engine import get_db
    async with get_db() as db:
        row = await db.fetch_one("PRAGMA user_version")
    assert row is not None


@pytest.mark.asyncio
async def test_sqlite_busy_timeout_configured(sqlite_db):
    from backend.db.engine import get_db
    async with get_db() as db:
        row = await db.fetch_one("PRAGMA busy_timeout")
    assert row is not None
    assert list(row.values())[0] >= 5000


@pytest.mark.asyncio
async def test_fetch_one_still_limits_selects(sqlite_db):
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute("INSERT INTO t (val) VALUES (?)", ("a",))
        await db.execute("INSERT INTO t (val) VALUES (?)", ("b",))
        await db.commit()
        row = await db.fetch_one("SELECT * FROM t ORDER BY id")
    assert row["val"] == "a"


# ─── Deletion: atomic claim + dry-run must not mutate status ──────────────────

@pytest_asyncio.fixture
async def queue_db(tmp_path):
    import backend.db.engine as eng
    import backend.db.utils as db_utils
    path = str(tmp_path / "queue.db")
    orig_engine, orig_utils = eng.SQLITE_PATH, db_utils.DB_PATH
    eng.SQLITE_PATH = path
    db_utils.DB_PATH = path
    import backend.db.settings_store as _ss
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    from backend.db.schema import init_db
    await init_db()
    yield
    eng.SQLITE_PATH = orig_engine
    db_utils.DB_PATH = orig_utils
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0


async def _insert_due_item(title="Old Movie") -> int:
    from backend.db.engine import get_db
    async with get_db() as db:
        item_id = await db.execute(
            "INSERT INTO media_queue "
            "(emby_id, title, media_type, status, delete_at, detected_at, "
            " library_id, library_name, file_path) "
            "VALUES (?, ?, 'Movie', 'pending', "
            "'2000-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00', '', '', '')",
            (f"emby-{title}", title),
        )
        await db.commit()
    return item_id


@pytest.mark.asyncio
async def test_claim_pending_is_atomic(queue_db):
    from backend.deletion import _claim_pending
    item_id = await _insert_due_item()
    assert await _claim_pending(item_id) is True
    assert await _claim_pending(item_id) is False, (
        "second claim on the same item must fail — prevents double deletion"
    )


@pytest.mark.asyncio
async def test_dry_run_does_not_mark_items_deleted(queue_db):
    from backend.db.settings_store import set_setting
    from backend.db.engine import get_db
    from backend.deletion import run_deletion

    await set_setting("dry_run", "true")
    item_id = await _insert_due_item("DryRun Movie")

    await run_deletion()

    async with get_db() as db:
        row = await db.fetch_one("SELECT status FROM media_queue WHERE id=?", (item_id,))
    assert row["status"] == "pending", (
        "dry-run must simulate only — queue status must stay 'pending'"
    )


@pytest.mark.asyncio
async def test_delete_now_dry_run_does_not_mark_deleted(queue_db):
    """delete-now in dry_run mode must simulate only — item stays pending."""
    from unittest.mock import AsyncMock, patch
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    from backend.db.settings_store import set_setting
    from backend.db.engine import get_db

    await set_setting("dry_run", "true")
    item_id = await _insert_due_item("DeleteNow DryRun")

    from backend.routers import media as media_router
    app = FastAPI()
    app.include_router(media_router.router)
    # Override the exact symbol the router holds — module reloads done by other
    # test fixtures can make backend.auth.require_auth a different object.
    app.dependency_overrides[media_router.require_auth] = lambda: "testuser"

    transport = ASGITransport(app=app)
    with patch("backend.routers.media._delete_media", new_callable=AsyncMock) as fake_del:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(f"/api/media/{item_id}/delete-now")

    assert resp.status_code == 200
    assert not any(
        call.args[1] is False or call.kwargs.get("dry_run") is False
        for call in fake_del.call_args_list
    ), "real deletion must never run in dry_run mode"

    async with get_db() as db:
        row = await db.fetch_one("SELECT status FROM media_queue WHERE id=?", (item_id,))
    assert row["status"] == "pending", (
        "dry_run delete-now must leave the item pending — marking it deleted "
        "removes it from the pipeline without any file deletion"
    )


@pytest.mark.asyncio
async def test_stale_deleting_items_reset_at_startup(queue_db):
    """Items stuck in 'deleting' (crash mid-deletion) must be recovered."""
    from backend.db.engine import get_db
    from backend.deletion import reset_stale_deleting

    item_id = await _insert_due_item("Crashed Mid-Delete")
    async with get_db() as db:
        await db.execute_write(
            "UPDATE media_queue SET status='deleting' WHERE id=?", (item_id,)
        )
        await db.commit()

    n = await reset_stale_deleting()

    assert n == 1
    async with get_db() as db:
        row = await db.fetch_one("SELECT status FROM media_queue WHERE id=?", (item_id,))
    assert row["status"] == "pending"


# ─── LIKE wildcard escaping ───────────────────────────────────────────────────

def test_escape_like_escapes_wildcards():
    from backend.db.utils import escape_like
    assert escape_like("50%") == "50!%"
    assert escape_like("a_b") == "a!_b"
    assert escape_like("yes!") == "yes!!"
    assert escape_like("plain") == "plain"


# ─── Plex webhook: path-based endpoint ───────────────────────────────────────

@pytest.mark.asyncio
async def test_webhook_path_accepted_with_correct_secret(wh_client):
    from backend.db.settings_store import set_setting
    await set_setting("plex_webhook_secret", "path-secret")
    resp = await wh_client.post(
        "/api/plex/webhook/path-secret", data={"payload": _scrobble_payload()}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_webhook_path_rejected_with_wrong_secret(wh_client):
    from backend.db.settings_store import set_setting
    await set_setting("plex_webhook_secret", "path-secret")
    resp = await wh_client.post(
        "/api/plex/webhook/wrong-secret", data={"payload": _scrobble_payload()}
    )
    assert resp.status_code == 403


# ─── Backup path validation ───────────────────────────────────────────────────

def test_backup_path_rejects_etc():
    from backend.backup import _validate_backup_path
    with pytest.raises(ValueError, match="system directory"):
        _validate_backup_path("/etc/cron.d")


def test_backup_path_rejects_root():
    from backend.backup import _validate_backup_path
    with pytest.raises(ValueError, match="system directory"):
        _validate_backup_path("/root/.ssh")


def test_backup_path_rejects_traversal():
    from backend.backup import _validate_backup_path
    with pytest.raises(ValueError, match="\\.\\."):
        _validate_backup_path("/app/data/../../../etc")


def test_backup_path_accepts_data_dir():
    from backend.backup import _validate_backup_path
    _validate_backup_path("/app/data/backups")  # must not raise


def test_backup_path_accepts_custom_dir():
    from backend.backup import _validate_backup_path
    _validate_backup_path("/backup/hygie")  # must not raise


# ─── jobs/history limit is bounded ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_jobs_history_limit_is_bounded(tmp_path):
    """GET /api/jobs/history?limit=9999999 must be rejected with 422."""
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _ss
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport

    db_path = str(tmp_path / "sched.db")
    orig_engine, orig_utils = _eng.SQLITE_PATH, _db_utils.DB_PATH
    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0

    from backend.db.schema import init_db
    await init_db()

    from backend.routers import scheduler as sched_router
    app = FastAPI()
    app.include_router(sched_router.router)
    app.dependency_overrides[sched_router.require_auth] = lambda: "testuser"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/jobs/history?limit=9999999")

    _eng.SQLITE_PATH = orig_engine
    _db_utils.DB_PATH = orig_utils
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0

    assert resp.status_code == 422, f"Expected 422 for out-of-range limit, got {resp.status_code}"


# ─── SSRF guard on admin "test connection" endpoints ──────────────────────────
# /api/settings/test-arr, /sync-arr-from-seerr and /media-servers/{id}/test let
# an authenticated admin make the *server* issue an HTTP request to an
# arbitrary URL and read the response back — unlike the image proxy, these
# URLs aren't whitelisted (testing a not-yet-saved server is the whole point).
# Block loopback/link-local (localhost, the 169.254.169.254 cloud metadata
# endpoint) the same way proxy.py does; RFC1918 LAN addresses stay allowed
# since real Radarr/Sonarr/Emby instances commonly live there.

class _ForbiddenAsyncClient:
    """httpx.AsyncClient that records whether it was ever instantiated.

    Raises on construction too, but the call sites' own broad `except
    Exception` would otherwise swallow that into a generic {"ok": False}
    result that looks like a pass even when the guard never fired — the
    `instantiated` flag is what actually proves no request was attempted.
    """
    instantiated = False

    def __init__(self, *a, **kw):
        type(self).instantiated = True
        raise RuntimeError("must not make an HTTP request to a blocked host")


@pytest.mark.asyncio
async def test_test_arr_instance_blocks_loopback(monkeypatch):
    import backend.services.arr_service as svc
    _ForbiddenAsyncClient.instantiated = False
    monkeypatch.setattr(svc.httpx, "AsyncClient", _ForbiddenAsyncClient)
    result = await svc.test_arr_instance("radarr", "http://127.0.0.1:7878", "key")
    assert _ForbiddenAsyncClient.instantiated is False, "guard did not fire before the HTTP call"
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_test_arr_instance_blocks_cloud_metadata(monkeypatch):
    import backend.services.arr_service as svc
    _ForbiddenAsyncClient.instantiated = False
    monkeypatch.setattr(svc.httpx, "AsyncClient", _ForbiddenAsyncClient)
    result = await svc.test_arr_instance("radarr", "http://169.254.169.254/latest/meta-data/", "key")
    assert _ForbiddenAsyncClient.instantiated is False, "guard did not fire before the HTTP call"
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_test_arr_instance_allows_lan_address(monkeypatch):
    """RFC1918 LAN addresses must still work — real Radarr/Sonarr live there."""
    import backend.services.arr_service as svc

    class _OkResponse:
        status_code = 200
        def json(self): return {"version": "5.0"}

    class _OkClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **kw): return _OkResponse()

    monkeypatch.setattr(svc.httpx, "AsyncClient", _OkClient)
    result = await svc.test_arr_instance("radarr", "http://192.168.1.50:7878", "key")
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_sync_arr_from_seerr_blocks_loopback(monkeypatch):
    import backend.services.arr_service as svc
    _ForbiddenAsyncClient.instantiated = False
    monkeypatch.setattr(svc.httpx, "AsyncClient", _ForbiddenAsyncClient)
    with pytest.raises(ValueError):
        await svc.sync_arr_from_seerr("http://127.0.0.1:5055", "key")
    assert _ForbiddenAsyncClient.instantiated is False, "guard did not fire before the HTTP call"


@pytest.mark.asyncio
@respx.mock
async def test_sync_arr_from_seerr_blocks_redirect_to_loopback():
    """sync_arr_from_seerr uses follow_redirects=True — the initial-URL guard
    alone is not enough: a Seerr server (or anyone who can spoof its DNS/
    responses) could 302 the request to an internal target after the guard
    already ran once. Every hop must be re-checked, the same way proxy.py
    re-validates redirects for the image proxy."""
    import backend.services.arr_service as svc

    redirect_route = respx.get("http://seerr.example.com/api/v1/settings/radarr").mock(
        return_value=httpx.Response(302, headers={"Location": "http://127.0.0.1:8000/admin/secrets"})
    )
    internal_route = respx.get("http://127.0.0.1:8000/admin/secrets").mock(
        return_value=httpx.Response(200, json=[])
    )

    with pytest.raises(Exception):
        await svc.sync_arr_from_seerr("http://seerr.example.com", "key")

    assert redirect_route.called, "the initial (allowed) request should still go out"
    assert not internal_route.called, "the redirect target must be re-validated and blocked"


@pytest.mark.asyncio
async def test_test_media_server_blocks_loopback(monkeypatch, tmp_path):
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _ss

    db_path = str(tmp_path / "ms.db")
    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0

    from backend.db.schema import init_db
    await init_db()
    from backend.db.media_servers import save_media_servers

    await save_media_servers([{
        "id": "0", "name": "evil", "url": "http://127.0.0.1:8096",
        "api_key": "k", "type": "emby", "enabled": True,
    }])

    import backend.routers.settings as settings_router

    async def _forbidden_test_emby(*a, **kw):
        raise AssertionError("must not contact a blocked host")

    monkeypatch.setattr(settings_router, "test_emby", _forbidden_test_emby)

    result = await settings_router.test_media_server(server_id="0", user="testuser")
    assert result["ok"] is False

    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0


# ─── Public dashboard: slug comparison ────────────────────────────────────────

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

    db_path = str(tmp_path / "public.db")
    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0

    from backend.db.schema import init_db
    await init_db()

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
async def test_public_upcoming_rejects_wrong_slug(public_client):
    from backend.db.settings_store import set_setting
    await set_setting("public_dashboard_enabled", "true")
    await set_setting("public_dashboard_slug", "myslug")
    resp = await public_client.get("/api/public/upcoming", params={"slug": "wrong"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_public_upcoming_accepts_correct_slug(public_client):
    from backend.db.settings_store import set_setting
    await set_setting("public_dashboard_enabled", "true")
    await set_setting("public_dashboard_slug", "myslug")
    resp = await public_client.get("/api/public/upcoming", params={"slug": "myslug"})
    assert resp.status_code == 200
