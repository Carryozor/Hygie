"""Tests for security/reliability hardening fixes.

Covers: SSRF proxy (port + redirect validation), Plex webhook fail-closed secret,
atomic deletion claim, dry-run status preservation, SQLite busy_timeout,
fetch_one heuristic, LIKE wildcard escaping.
"""
import os
import json
import pytest
import pytest_asyncio

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

@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def wh_client(tmp_path_factory):
    import importlib
    from httpx import AsyncClient, ASGITransport
    import backend.db.engine as _eng
    db_path = str(tmp_path_factory.mktemp("hardening") / "wh.db")
    _eng.SQLITE_PATH = db_path
    import backend.db.settings_store as _ss
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    import backend.main as main_mod
    importlib.reload(main_mod)
    app = main_mod.app
    async with main_mod.lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


def _scrobble_payload() -> str:
    return json.dumps({
        "event": "media.scrobble",
        "Account": {"id": 1, "title": "testuser"},
        "Metadata": {"ratingKey": "101", "title": "Inception", "lastViewedAt": 1700000000},
    })


@pytest.mark.asyncio(loop_scope="module")
async def test_webhook_rejected_when_no_secret_configured(wh_client):
    resp = await wh_client.post(
        "/api/plex/webhook", data={"payload": _scrobble_payload()}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio(loop_scope="module")
async def test_webhook_rejected_with_wrong_secret(wh_client):
    from backend.db.settings_store import set_setting
    await set_setting("plex_webhook_secret", "s3cret-token")
    resp = await wh_client.post(
        "/api/plex/webhook?secret=wrong", data={"payload": _scrobble_payload()}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio(loop_scope="module")
async def test_webhook_accepted_with_correct_secret(wh_client):
    from backend.db.settings_store import set_setting
    await set_setting("plex_webhook_secret", "s3cret-token")
    resp = await wh_client.post(
        "/api/plex/webhook?secret=s3cret-token", data={"payload": _scrobble_payload()}
    )
    assert resp.status_code == 200


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
            "INSERT INTO media_queue (emby_id, title, media_type, status, delete_at, detected_at, library_id) "
            "VALUES (?, ?, 'Movie', 'pending', '2000-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00', '')",
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


# ─── LIKE wildcard escaping ───────────────────────────────────────────────────

def test_escape_like_escapes_wildcards():
    from backend.db.utils import escape_like
    assert escape_like("50%") == r"50\%"
    assert escape_like("a_b") == r"a\_b"
    assert escape_like("back\\slash") == "back\\\\slash"
    assert escape_like("plain") == "plain"
