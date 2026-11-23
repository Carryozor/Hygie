"""Refresh vs logout-all / change-password race (4.5.1).

A logout-all landing between the refresh-token check and the mint of the new
refresh token must not leave a live session behind.
"""
import asyncio
import contextlib
import os

import aiosqlite
import pytest_asyncio

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")
os.environ.pop("DATABASE_URL", None)

PWD = "supersecret123"
COOKIE = "hygie_refresh"


@pytest_asyncio.fixture
async def env(tmp_path):
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.auth as auth_mod

    db_path = str(tmp_path / "r.db")
    orig_engine, orig_utils = _eng.SQLITE_PATH, _db_utils.DB_PATH
    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    auth_mod._rate_buckets.clear()
    from backend.db.schema import init_db
    await init_db()
    await auth_mod.create_user("alice", PWD)
    yield auth_mod, db_path
    _eng.SQLITE_PATH = orig_engine
    _db_utils.DB_PATH = orig_utils
    auth_mod._rate_buckets.clear()


def _app():
    from fastapi import FastAPI
    from backend.routers import auth as auth_router
    app = FastAPI()
    app.include_router(auth_router.router)
    return app


async def _live_tokens_except(path, auth, raw):
    async with aiosqlite.connect(path) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM refresh_tokens WHERE revoked=0 AND token_hash!=?",
            (auth._hash_token(raw),),
        ) as cur:
            return (await cur.fetchone())[0]


def _spy_get_db(monkeypatch, auth):
    """Record, per get_db() block, the SQL statements executed."""
    blocks = []
    real = auth.get_db

    @contextlib.asynccontextmanager
    async def spy():
        stmts = []
        blocks.append(stmts)
        async with real() as db:
            class Proxy:
                def __getattr__(self, name):
                    attr = getattr(db, name)
                    if name in ("execute", "execute_write", "fetch_one", "fetch_all"):
                        async def wrapped(sql, params=()):
                            stmts.append(" ".join(sql.split()))
                            return await attr(sql, params)
                        return wrapped
                    return attr
            yield Proxy()
    monkeypatch.setattr(auth, "get_db", spy)
    return blocks


# 1 - deterministic race through the router
async def test_logout_all_landing_right_after_verify_blocks_the_refresh(env, monkeypatch):
    from httpx import AsyncClient, ASGITransport
    from backend.routers import auth as auth_router
    auth, path = env
    real_verify = auth_router.verify_refresh_token
    calls = []

    async def verify_then_logout_all(raw):
        result = await real_verify(raw)
        calls.append(raw)
        await auth.revoke_all_refresh_tokens("alice")     # the racing logout-all
        return result
    monkeypatch.setattr(auth_router, "verify_refresh_token", verify_then_logout_all)

    raw = await auth.create_refresh_token("alice")
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as c:
        c.cookies.set(COOKIE, raw, path="/api/auth")
        r = await c.post("/api/auth/refresh", json={})
    assert calls == [raw]
    assert r.status_code == 401
    assert await _live_tokens_except(path, auth, raw) == 0


# 2 - revoke_all_refresh_tokens: one block, users first
async def test_revoke_all_is_a_single_block_starting_with_users_update(env, monkeypatch):
    auth, path = env
    raw = await auth.create_refresh_token("alice")
    blocks = _spy_get_db(monkeypatch, auth)
    await auth.revoke_all_refresh_tokens("alice")
    assert len(blocks) == 1
    assert blocks[0][0].startswith("UPDATE users SET token_version = token_version + 1")
    assert blocks[0][1].startswith("UPDATE refresh_tokens SET revoked=1")
    assert await auth.verify_refresh_token(raw) is None
    assert await auth.current_token_version("alice") == 1


async def test_revoke_all_unknown_user_is_a_noop(env):
    auth, _ = env
    await auth.revoke_all_refresh_tokens("ghost")
    assert await auth.current_token_version("alice") == 0


# 3 - create_refresh_token_if_version
async def test_if_version_takes_the_row_lock_first_then_inserts(env, monkeypatch):
    auth, path = env
    blocks = _spy_get_db(monkeypatch, auth)
    raw = await auth.create_refresh_token_if_version("alice", 0)
    assert raw
    assert len(blocks) == 1
    assert blocks[0][0] == "UPDATE users SET token_version = token_version WHERE username=?"
    assert blocks[0][1].startswith("SELECT id, token_version FROM users")
    assert blocks[0][2].startswith("INSERT INTO refresh_tokens")
    assert (await auth.verify_refresh_token(raw))[0] == "alice"


async def test_if_version_stale_returns_none_and_inserts_nothing(env):
    auth, path = env
    await auth.invalidate_access_tokens("alice")
    assert await auth.create_refresh_token_if_version("alice", 0) is None
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT COUNT(*) FROM refresh_tokens") as cur:
            assert (await cur.fetchone())[0] == 0


async def test_if_version_unknown_user_returns_none(env):
    auth, _ = env
    assert await auth.create_refresh_token_if_version("ghost", 0) is None


async def test_verify_refresh_token_returns_username_and_version_snapshot(env):
    auth, _ = env
    await auth.invalidate_access_tokens("alice")
    await auth.invalidate_access_tokens("alice")
    raw = await auth.create_refresh_token("alice")
    assert await auth.verify_refresh_token(raw) == ("alice", 2)


# 4 - stochastic, real interleaving through ASGI
async def _login(c, password):
    r = await c.post("/api/auth/login", json={"username": "alice", "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def _trial(app, i, action, password):
    from httpx import AsyncClient, ASGITransport
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://v") as victim, \
               AsyncClient(transport=transport, base_url="http://a") as attacker:
        v_tok = await _login(victim, password)
        await _login(attacker, password)
        stop = asyncio.Event()
        stats = {"ok": 0, "statuses": set(), "last": None}

        async def loop():
            while not stop.is_set():
                r = await attacker.post("/api/auth/refresh", json={})
                stats["statuses"].add(r.status_code)
                if r.status_code != 200:
                    return
                stats["ok"] += 1
                stats["last"] = r.json()["access_token"]
        task = asyncio.create_task(loop())
        await asyncio.sleep(0.01 + (i % 7) * 0.002)
        hdr = {"Authorization": f"Bearer {v_tok}"}
        if action == "logout-all":
            r = await victim.post("/api/auth/logout-all", headers=hdr)
        else:
            r = await victim.post("/api/auth/change-password", headers=hdr,
                                  json={"current_password": password, "new_password": password + "x"})
        assert r.status_code == 200, r.text
        stop.set()
        await task
        survived = False
        r = await attacker.post("/api/auth/refresh", json={})
        stats["statuses"].add(r.status_code)
        if r.status_code == 200:
            stats["ok"] += 1
            me = await attacker.get("/api/auth/me",
                                    headers={"Authorization": f"Bearer {r.json()['access_token']}"})
            survived = me.status_code == 200
        if stats["last"]:
            me = await attacker.get("/api/auth/me", headers={"Authorization": f"Bearer {stats['last']}"})
            survived = survived or me.status_code == 200
        return survived, stats


async def test_stochastic_no_session_survives_logout_all_or_password_change(env, monkeypatch):
    from backend.routers import auth as auth_router
    monkeypatch.setattr(auth_router, "rate_limit_attempt", lambda key: (False, "t"))
    app = _app()
    survivors, refresh_ok, trials = 0, 0, 0
    password = PWD
    for i in range(40):
        s, st = await _trial(app, i, "logout-all", password)
        survivors += s
        refresh_ok += st["ok"]
        trials += 1
        assert st["statuses"] <= {200, 401}
    for i in range(20):
        s, st = await _trial(app, i, "change-password", password)
        password += "x"
        survivors += s
        refresh_ok += st["ok"]
        trials += 1
        assert st["statuses"] <= {200, 401}
    assert refresh_ok >= trials          # the attacker really was refreshing
    assert survivors == 0
