"""reset_mfa CLI: break-glass removal of a user's 2FA."""
import os
import time

import aiosqlite
import pytest
import pytest_asyncio

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")
os.environ.pop("DATABASE_URL", None)

from backend import mfa, totp  # noqa: E402
from backend.tools import reset_mfa  # noqa: E402


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.auth as auth_mod

    db_path = str(tmp_path / "cli.db")
    monkeypatch.setattr(_eng, "SQLITE_PATH", db_path)
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    from backend.db.schema import init_db
    await init_db()
    await auth_mod.create_user("alice", "supersecret123")
    uid = (await auth_mod.get_user("alice"))["id"]
    events = []

    async def spy(event, *, ip, username, method, reason="ok", notify=False):
        events.append((event, ip, username, method, reason))
    monkeypatch.setattr(reset_mfa, "log_auth_event", spy)
    return auth_mod, uid, db_path, events


async def _enable(uid):
    s = await mfa.begin_setup(uid, "alice")
    await mfa.enable(uid, totp.code_at(s.secret, int(time.time()) // 30))


async def test_reset_disables_and_revokes(env, capsys):
    auth_mod, uid, path, events = env
    await _enable(uid)
    refresh = await auth_mod.create_refresh_token("alice")
    old_access = await auth_mod.issue_access_token("alice")
    await mfa.create_challenge(uid, 0)

    assert await reset_mfa.run("alice", yes=True) == 0

    st = await mfa.get_state(uid)
    assert not st.enabled and not st.pending and st.backup_codes_remaining == 0
    assert await auth_mod.verify_refresh_token(refresh) is None
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials
    with pytest.raises(HTTPException):
        await auth_mod.require_auth(HTTPAuthorizationCredentials(scheme="Bearer", credentials=old_access))
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT COUNT(*) FROM mfa_challenges") as cur:
            assert (await cur.fetchone())[0] == 0
    assert events == [("mfa_reset", "cli", "alice", "cli", "ok")]
    assert "2FA" in capsys.readouterr().out


async def test_unknown_user_exit_1(env):
    assert await reset_mfa.run("ghost", yes=True) == 1
    assert env[3] == []


async def test_mfa_not_enabled_exit_2(env):
    assert await reset_mfa.run("alice", yes=True) == 2
    assert env[3] == []


async def test_confirmation_declined_changes_nothing(env):
    _, uid, _, events = env
    await _enable(uid)
    assert await reset_mfa.run("alice", yes=False, input_fn=lambda prompt: "n") == 4
    assert (await mfa.get_state(uid)).enabled and events == []
    assert await reset_mfa.run("alice", yes=False, input_fn=lambda prompt: "oui") == 0
    assert not (await mfa.get_state(uid)).enabled


async def test_refuses_missing_sqlite_file(tmp_path, monkeypatch):
    import backend.db.engine as _eng
    missing = tmp_path / "nope.db"
    monkeypatch.setattr(_eng, "SQLITE_PATH", str(missing))
    assert await reset_mfa.run("alice", yes=True) == 3
    assert not missing.exists()


async def test_db_error_exit_3(env, monkeypatch):
    _, uid, _, _ = env
    await _enable(uid)

    async def boom(user_id):
        raise RuntimeError("db down")
    monkeypatch.setattr(reset_mfa.mfa, "disable", boom)
    assert await reset_mfa.run("alice", yes=True) == 3


def test_embedded_mariadb_url_from_pass_file(tmp_path):
    pf = tmp_path / ".mariadb_pass"
    pf.write_text("p@ss/w:rd\n")
    url = reset_mfa.resolve_database_url({"EMBEDDED_MARIADB": "true"}, str(pf))
    assert url == "mysql+aiomysql://hygie:p%40ss%2Fw%3Ard@127.0.0.1:3306/hygie"


def test_database_url_env_wins(tmp_path):
    assert reset_mfa.resolve_database_url({"DATABASE_URL": " mysql+aiomysql://u:p@h/db "}, "/nonexistent") \
        == "mysql+aiomysql://u:p@h/db"


def test_no_database_url_for_sqlite_deployment(tmp_path):
    assert reset_mfa.resolve_database_url({}, str(tmp_path / "x")) is None


def test_embedded_without_pass_file_raises(tmp_path):
    with pytest.raises(reset_mfa.ConfigError):
        reset_mfa.resolve_database_url({"EMBEDDED_MARIADB": "true"}, str(tmp_path / "missing"))


def test_main_exit_code(monkeypatch):
    async def fake_run(username, yes, input_fn=input):
        return 2
    monkeypatch.setattr(reset_mfa, "run", fake_run)
    monkeypatch.setattr(reset_mfa, "_prepare_environment", lambda: None)
    with pytest.raises(SystemExit) as e:
        reset_mfa.main(["--username", "alice", "--yes"])
    assert e.value.code == 2
