"""Coverage-focused tests for backend/auth.py's untested branches.

tests/test_auth.py covers password hashing, JWT round-trips, the in-memory
rate limiter and get_client_ip. tests/test_auth_cookie.py and
tests/test_rate_limit_check_vs_record.py cover the HTTP-level refresh/rotate/
revoke flows through TestClient. This file fills what's left:
  - secret loading branches (env var, existing file, unreadable file)
  - verify_password's generic-exception branch (malformed hash string)
  - verify_token rejecting a non-"access" token type
  - create_refresh_token / verify_refresh_token / revoke_all_refresh_tokens
    edge cases (unknown user, expired token)
  - require_auth's two 401 branches called directly (not via HTTP)
  - create_user / get_user / update_password
  - rate_limit() / rate_limit_attempt() / release_attempt()'s MariaDB-dispatch
    and on-disk-SQLite-error fallback branches (test_auth.py's rate-limit
    tests all run with DB_PATH==":memory:", which takes neither branch)
"""
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

import backend.auth as auth_mod


@pytest.fixture(scope="module", autouse=True)
def fast_argon2():
    from argon2 import PasswordHasher
    orig = auth_mod._ph
    auth_mod._ph = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    yield
    auth_mod._ph = orig


@pytest_asyncio.fixture
async def db_path(monkeypatch, tmp_path):
    """A real on-disk SQLite DB with the full schema — needed for anything
    that touches users/refresh_tokens/rate_limit via engine.get_db() or
    auth.py's own stdlib sqlite3 rate-limit path (both keyed off DB_PATH,
    which auth.py imported as a plain name — patching db.utils.DB_PATH alone
    would NOT reach it)."""
    import backend.db.schema as _db_schema
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _db_ss
    import backend.db.engine as _db_engine

    path = str(tmp_path / "test.db")
    monkeypatch.setattr(_db_schema, "DB_PATH", path)
    monkeypatch.setattr(_db_utils, "DB_PATH", path)
    monkeypatch.setattr(_db_ss, "DB_PATH", path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", path)
    monkeypatch.setattr(auth_mod, "DB_PATH", path)  # auth.py's own bound copy
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()
    return path


# ─── _load_or_create_secret ─────────────────────────────────────────────────────

def test_load_secret_returns_env_key_when_long_enough(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    assert auth_mod._load_or_create_secret() == "x" * 40


def test_load_secret_ignores_short_env_key_and_reads_file(monkeypatch, tmp_path):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    secret_file = tmp_path / ".secret"
    secret_file.write_text("y" * 40)
    monkeypatch.setattr(auth_mod, "SECRET_FILE", str(secret_file))
    assert auth_mod._load_or_create_secret() == "y" * 40


def test_load_secret_generates_and_persists_when_no_file(monkeypatch, tmp_path):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    secret_file = tmp_path / "subdir" / ".secret"
    monkeypatch.setattr(auth_mod, "SECRET_FILE", str(secret_file))
    key = auth_mod._load_or_create_secret()
    assert len(key) >= 32
    assert secret_file.read_text() == key


def test_load_secret_falls_through_when_file_read_raises(monkeypatch, tmp_path, caplog):
    """A SECRET_FILE that exists but can't be read as text (e.g. it's a
    directory) must be logged and treated as absent, not crash startup."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    secret_dir = tmp_path / ".secret"
    secret_dir.mkdir()  # open(dir, "r") raises IsADirectoryError
    monkeypatch.setattr(auth_mod, "SECRET_FILE", str(secret_dir))
    with caplog.at_level(logging.WARNING):
        key = auth_mod._load_or_create_secret()
    assert len(key) >= 32
    assert any("Could not read secret file" in r.message for r in caplog.records)


# ─── verify_password generic exception branch ──────────────────────────────────

def test_verify_password_malformed_hash_returns_false_via_generic_exception():
    # Not a valid argon2 hash string -> argon2's InvalidHash, not VerifyMismatchError
    assert auth_mod.verify_password("whatever", "not-a-valid-argon2-hash") is False


# ─── verify_token: non-access type rejected ────────────────────────────────────

def test_verify_token_rejects_non_access_type():
    import jwt as _pyjwt
    payload = {
        "sub": "alice", "type": "refresh",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }
    token = _pyjwt.encode(payload, auth_mod.SECRET_KEY, algorithm=auth_mod.ALGORITHM)
    assert auth_mod.verify_token(token) is None


def test_verify_token_accepts_missing_type_claim():
    """Tokens with no 'type' claim at all (legacy/hand-crafted) are accepted —
    only an explicit non-'access' type is rejected."""
    import jwt as _pyjwt
    payload = {"sub": "alice", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}
    token = _pyjwt.encode(payload, auth_mod.SECRET_KEY, algorithm=auth_mod.ALGORITHM)
    assert auth_mod.verify_token(token) == "alice"


# ─── refresh tokens ─────────────────────────────────────────────────────────────

async def test_create_refresh_token_raises_for_unknown_user(db_path):
    with pytest.raises(ValueError, match="not found"):
        await auth_mod.create_refresh_token("nobody")


async def test_verify_refresh_token_returns_none_when_expired(db_path):
    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            ("alice", "h", "2020-01-01"),
        )
        await db.commit()
        async with db.execute("SELECT id FROM users WHERE username='alice'") as cur:
            (user_id,) = await cur.fetchone()
        expired = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        await db.execute(
            "INSERT INTO refresh_tokens (user_id, token_hash, expires_at, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, auth_mod._hash_token("raw-expired"), expired, "2020-01-01"),
        )
        await db.commit()
    assert await auth_mod.verify_refresh_token("raw-expired") is None


async def test_verify_refresh_token_returns_none_for_unknown_token(db_path):
    assert await auth_mod.verify_refresh_token("never-issued") is None


async def test_revoke_all_refresh_tokens_revokes_every_active_token(db_path):
    await auth_mod.create_user("alice", "pw")
    t1 = await auth_mod.create_refresh_token("alice")
    t2 = await auth_mod.create_refresh_token("alice")
    await auth_mod.revoke_all_refresh_tokens("alice")
    assert await auth_mod.verify_refresh_token(t1) is None
    assert await auth_mod.verify_refresh_token(t2) is None


async def test_revoke_all_refresh_tokens_noop_for_unknown_user(db_path):
    await auth_mod.revoke_all_refresh_tokens("ghost")  # must not raise


# ─── require_auth ────────────────────────────────────────────────────────────────

async def test_require_auth_raises_401_when_no_credentials():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await auth_mod.require_auth(credentials=None)
    assert exc_info.value.status_code == 401
    assert "requis" in exc_info.value.detail


async def test_require_auth_raises_401_for_invalid_token():
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="garbage")
    with pytest.raises(HTTPException) as exc_info:
        await auth_mod.require_auth(credentials=creds)
    assert exc_info.value.status_code == 401
    assert "invalide" in exc_info.value.detail


async def test_require_auth_returns_username_for_valid_token():
    from fastapi.security import HTTPAuthorizationCredentials
    token = auth_mod.create_access_token("alice")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    assert await auth_mod.require_auth(credentials=creds) == "alice"


# ─── user management ────────────────────────────────────────────────────────────

async def test_create_user_and_get_user_round_trip(db_path):
    await auth_mod.create_user("bob", "s3cret")
    user = await auth_mod.get_user("bob")
    assert user is not None
    assert user["username"] == "bob"
    assert auth_mod.verify_password("s3cret", user["password_hash"])


async def test_get_user_returns_none_when_missing(db_path):
    assert await auth_mod.get_user("nope") is None


async def test_user_exists_false_then_true(db_path):
    assert await auth_mod.user_exists() is False
    await auth_mod.create_user("bob", "s3cret")
    assert await auth_mod.user_exists() is True


async def test_update_password_changes_hash(db_path):
    await auth_mod.create_user("bob", "old-pass")
    await auth_mod.update_password("bob", "new-pass")
    user = await auth_mod.get_user("bob")
    assert auth_mod.verify_password("new-pass", user["password_hash"])
    assert not auth_mod.verify_password("old-pass", user["password_hash"])


# ─── rate_limit() / rate_limit_attempt() / release_attempt(): dispatch branches ──

def _key():
    return f"disk-{time.time()}-{id(object())}"


def test_rate_limit_dispatches_to_mariadb_backend_when_dialect_is_mariadb(db_path, monkeypatch):
    import backend.db.engine as engine
    monkeypatch.setattr(engine, "DIALECT", "mariadb")
    calls = {}

    def fake_mariadb_rate_limit(key, now, cutoff, max_):
        calls["args"] = (key, max_)
        return True

    import backend._rate_limit_backend as rlb
    monkeypatch.setattr(rlb, "mariadb_rate_limit", fake_mariadb_rate_limit)
    assert auth_mod.rate_limit(_key()) is True
    assert calls["args"][1] == auth_mod.RATE_LIMIT_MAX


def test_rate_limit_falls_back_to_memory_when_mariadb_backend_raises(db_path, monkeypatch, caplog):
    import backend.db.engine as engine
    monkeypatch.setattr(engine, "DIALECT", "mariadb")

    def boom(key, now, cutoff, max_):
        raise RuntimeError("mariadb down")

    import backend._rate_limit_backend as rlb
    monkeypatch.setattr(rlb, "mariadb_rate_limit", boom)
    key = _key()
    with caplog.at_level(logging.WARNING):
        result = auth_mod.rate_limit(key)
    assert result is False  # first attempt, memory fallback allows it
    assert any("falling back to in-memory" in r.message for r in caplog.records)


def test_rate_limit_falls_back_to_memory_on_sqlite_file_error(monkeypatch, caplog):
    """DB_PATH points at a real file path but the rate_limit table/db is
    unusable (e.g. points at a directory) — must fall back, not raise."""
    import backend.db.engine as engine
    monkeypatch.setattr(engine, "DIALECT", "sqlite")
    monkeypatch.setattr(auth_mod, "DB_PATH", "/definitely/not/a/real/path/db.sqlite")
    key = _key()
    with caplog.at_level(logging.WARNING):
        result = auth_mod.rate_limit(key)
    assert result is False
    assert any("DB error, falling back to in-memory" in r.message for r in caplog.records)


def test_rate_limit_attempt_dispatches_to_mariadb_backend(db_path, monkeypatch):
    import backend.db.engine as engine
    monkeypatch.setattr(engine, "DIALECT", "mariadb")

    def fake(key, now, cutoff, max_):
        return (False, now)

    import backend._rate_limit_backend as rlb
    monkeypatch.setattr(rlb, "mariadb_rate_limit_attempt", fake)
    blocked, token = auth_mod.rate_limit_attempt(_key())
    assert blocked is False
    assert isinstance(token, float)


def test_rate_limit_attempt_falls_back_to_memory_when_mariadb_backend_raises(db_path, monkeypatch):
    import backend.db.engine as engine
    monkeypatch.setattr(engine, "DIALECT", "mariadb")

    def boom(key, now, cutoff, max_):
        raise RuntimeError("mariadb down")

    import backend._rate_limit_backend as rlb
    monkeypatch.setattr(rlb, "mariadb_rate_limit_attempt", boom)
    blocked, token = auth_mod.rate_limit_attempt(_key())
    assert blocked is False
    assert isinstance(token, float)


def test_rate_limit_attempt_falls_back_to_memory_on_sqlite_file_error(monkeypatch):
    import backend.db.engine as engine
    monkeypatch.setattr(engine, "DIALECT", "sqlite")
    monkeypatch.setattr(auth_mod, "DB_PATH", "/definitely/not/a/real/path/db.sqlite")
    blocked, token = auth_mod.rate_limit_attempt(_key())
    assert blocked is False
    assert isinstance(token, float)


def test_release_attempt_noop_when_token_is_none(db_path):
    auth_mod.release_attempt("k", None)  # must not raise, must not touch DB


def test_release_attempt_dispatches_to_mariadb_backend(db_path, monkeypatch):
    import backend.db.engine as engine
    monkeypatch.setattr(engine, "DIALECT", "mariadb")
    calls = []

    def fake(key, token):
        calls.append((key, token))

    import backend._rate_limit_backend as rlb
    monkeypatch.setattr(rlb, "mariadb_release_attempt", fake)
    auth_mod.release_attempt("mykey", 123.0)
    assert calls == [("mykey", 123.0)]


def test_release_attempt_falls_back_to_memory_when_mariadb_backend_raises(db_path, monkeypatch):
    import backend.db.engine as engine
    monkeypatch.setattr(engine, "DIALECT", "mariadb")

    def boom(key, token):
        raise RuntimeError("mariadb down")

    import backend._rate_limit_backend as rlb
    monkeypatch.setattr(rlb, "mariadb_release_attempt", boom)
    # must not raise — falls back to the (no-op on unknown token) memory path
    auth_mod.release_attempt("k", 123.0)


def test_release_attempt_falls_back_to_memory_on_sqlite_file_error(monkeypatch, caplog):
    import backend.db.engine as engine
    monkeypatch.setattr(engine, "DIALECT", "sqlite")
    monkeypatch.setattr(auth_mod, "DB_PATH", "/definitely/not/a/real/path/db.sqlite")
    with caplog.at_level(logging.WARNING):
        auth_mod.release_attempt("k", 123.0)  # must not raise
    assert any("DB error, falling back to in-memory" in r.message for r in caplog.records)


def test_rate_limit_real_sqlite_file_path_persists_across_calls(db_path):
    """Sanity check for the on-disk (non-:memory:) branch itself — separate
    from the in-memory-window tests in test_auth.py."""
    key = _key()
    for _ in range(auth_mod.RATE_LIMIT_MAX):
        assert auth_mod.rate_limit(key) is False
    assert auth_mod.rate_limit(key) is True
    with sqlite3.connect(db_path) as conn:
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM rate_limit WHERE key=?", (key,)
        ).fetchone()
    assert count == auth_mod.RATE_LIMIT_MAX + 1


def test_memory_rate_limit_prunes_stale_buckets_every_500th_call(monkeypatch):
    """Housekeeping branch: every 500th call sweeps buckets whose newest
    timestamp is already outside the window, bounding _rate_buckets' size."""
    key_stale = f"stale-{_key()}"
    key_fresh = f"fresh-{_key()}"
    auth_mod._rate_buckets[key_stale] = [100.0]   # newest entry <= cutoff below
    auth_mod._rate_buckets[key_fresh] = [10_000.0]
    monkeypatch.setattr(auth_mod, "_rate_call_counter", 499)

    auth_mod._memory_rate_limit(f"trigger-{_key()}", now=10_000.0, cutoff=9_000.0)

    assert key_stale not in auth_mod._rate_buckets
    assert key_fresh in auth_mod._rate_buckets


def test_memory_release_attempt_swallows_value_error_when_token_not_in_bucket():
    """A token that no longer matches any entry (already purged, or a caller
    handing back a stale token) must be a no-op, not raise."""
    key = _key()
    blocked, token = auth_mod._memory_rate_limit_attempt(key, now=1000.0, cutoff=0.0)
    assert blocked is False
    auth_mod._memory_release_attempt(key, token + 1)  # bucket has [1000.0], not 1001.0
    assert auth_mod._rate_buckets[key] == [1000.0]  # untouched — ValueError path taken


def test_rate_limit_attempt_real_sqlite_file_path_and_release(db_path):
    key = _key()
    blocked, token = auth_mod.rate_limit_attempt(key)
    assert blocked is False
    with sqlite3.connect(db_path) as conn:
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM rate_limit WHERE key=?", (key,)
        ).fetchone()
    assert count == 1
    auth_mod.release_attempt(key, token)
    with sqlite3.connect(db_path) as conn:
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM rate_limit WHERE key=?", (key,)
        ).fetchone()
    assert count == 0
