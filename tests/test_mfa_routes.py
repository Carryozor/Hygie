"""2FA HTTP routes: two-step login, /mfa/* management, auth events."""
import os
import types

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")
os.environ.pop("DATABASE_URL", None)

from backend import totp  # noqa: E402

PWD = "supersecret123"
COOKIE = "hygie_refresh"
T0 = 1_700_000_000.0


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.auth as auth_mod
    import backend.auth_events as ev
    from fastapi import FastAPI
    from backend.routers import auth as auth_router

    db_path = str(tmp_path / "r.db")
    orig_engine, orig_utils = _eng.SQLITE_PATH, _db_utils.DB_PATH
    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    # In-memory limiter: isolates this module from rate_limit rows left in a
    # shared DB file by other test modules (same keys: 127.0.0.1 + alice).
    monkeypatch.setattr(auth_mod, "DB_PATH", ":memory:")
    auth_mod._rate_buckets.clear()
    from backend.db.schema import init_db
    await init_db()
    await auth_mod.create_user("alice", PWD)

    clock = [T0]
    monkeypatch.setattr(totp, "time", types.SimpleNamespace(time=lambda: clock[0]))

    async def _no_notify(*a, **k):
        return None
    monkeypatch.setattr(ev, "_notify_login", _no_notify)

    app = FastAPI()
    app.include_router(auth_router.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield types.SimpleNamespace(c=c, auth=auth_mod, path=db_path, clock=clock, router=auth_router)
    _eng.SQLITE_PATH = orig_engine
    _db_utils.DB_PATH = orig_utils
    auth_mod._rate_buckets.clear()


@pytest.fixture
def events(env, monkeypatch):
    seen = []

    async def spy(event, *, ip, username, method, reason="ok", notify=False):
        seen.append(dict(event=event, ip=ip, username=username, method=method, reason=reason, notify=notify))
    monkeypatch.setattr(env.router, "log_auth_event", spy)
    return seen


async def _login(e, pw=PWD):
    return await e.c.post("/api/auth/login", json={"username": "alice", "password": pw})


async def _enable_mfa(e):
    """Log in, run setup+enable. Returns (access_token, secret, backup_codes)."""
    r = await _login(e)
    tok = r.json()["access_token"]
    r = await e.c.post("/api/auth/mfa/setup", json={"current_password": PWD}, headers=_bearer(tok))
    secret = r.json()["secret"]
    code = totp.code_at(secret, int(e.clock[0]) // 30)
    r = await e.c.post("/api/auth/mfa/enable", json={"code": code}, headers=_bearer(tok))
    assert r.status_code == 200, r.text
    return r.json()["access_token"], secret, r.json()["backup_codes"]


async def _challenge(e):
    r = await _login(e)
    assert r.json()["mfa_required"] is True
    return r.json()["mfa_token"]


def _next_code(e, secret):
    """A valid code for a step strictly newer than anything used so far."""
    e.clock[0] += 30
    return totp.code_at(secret, int(e.clock[0]) // 30)


async def _mfa_login(e, token, code):
    return await e.c.post("/api/auth/login/mfa", json={"mfa_token": token, "code": code})


# ── login ───────────────────────────────────────────────────────────────────

async def test_login_without_mfa_contract_unchanged(env):
    r = await _login(env)
    assert r.status_code == 200
    assert set(r.json()) == {"token", "access_token", "username"}
    assert COOKIE in r.cookies


async def test_login_with_mfa_returns_challenge_no_cookie_no_token(env):
    await _enable_mfa(env)
    env.c.cookies.clear()
    r = await _login(env)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"mfa_required", "mfa_token", "expires_in"}
    assert body["mfa_required"] is True and body["expires_in"] == 300 and len(body["mfa_token"]) >= 43
    assert "set-cookie" not in r.headers


async def test_login_mfa_success_sets_cookie(env):
    _, secret, _ = await _enable_mfa(env)
    env.c.cookies.clear()
    chal = await _challenge(env)
    r = await _mfa_login(env, chal, _next_code(env, secret))
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"token", "access_token", "username"} and r.json()["username"] == "alice"
    assert COOKIE in r.cookies
    me = await env.c.get("/api/auth/me", headers=_bearer(r.json()["access_token"]))
    assert me.status_code == 200


async def test_wrong_code_401_with_code_field(env):
    await _enable_mfa(env)
    chal = await _challenge(env)
    r = await _mfa_login(env, chal, "000000")
    assert r.status_code == 401
    assert r.json() == {"detail": "Code invalide", "code": "mfa_code_invalid"}


async def test_replayed_code_rejected(env):
    _, secret, _ = await _enable_mfa(env)
    code = _next_code(env, secret)
    assert (await _mfa_login(env, await _challenge(env), code)).status_code == 200
    r = await _mfa_login(env, await _challenge(env), code)
    assert r.status_code == 401 and r.json()["code"] == "mfa_code_invalid"


async def test_unknown_challenge_and_exhausted_challenge(env):
    _, secret, _ = await _enable_mfa(env)
    r = await _mfa_login(env, "nope", "123456")
    assert r.status_code == 401 and r.json()["code"] == "mfa_challenge_invalid"
    assert "reconnectez-vous" in r.json()["detail"]
    chal = await _challenge(env)
    for _ in range(3):
        assert (await _mfa_login(env, chal, "000000")).json()["code"] == "mfa_code_invalid"
    r = await _mfa_login(env, chal, _next_code(env, secret))
    assert r.status_code == 401 and r.json()["code"] == "mfa_challenge_invalid"


async def test_challenge_is_single_use(env):
    _, secret, _ = await _enable_mfa(env)
    chal = await _challenge(env)
    assert (await _mfa_login(env, chal, _next_code(env, secret))).status_code == 200
    r = await _mfa_login(env, chal, _next_code(env, secret))
    assert r.json()["code"] == "mfa_challenge_invalid"


async def test_mfa_lockout_per_account_across_ips(env, monkeypatch):
    _, secret, _ = await _enable_mfa(env)
    last = None
    for i in range(8):
        monkeypatch.setattr(env.router, "get_client_ip", lambda req, i=i: f"10.0.0.{i}")
        last = await _mfa_login(env, await _challenge(env), "000000")
        if last.status_code == 429:
            break
    assert last.status_code == 429 and last.json()["code"] == "mfa_locked"
    monkeypatch.setattr(env.router, "get_client_ip", lambda req: "10.9.9.9")
    r = await _mfa_login(env, await _challenge(env), _next_code(env, secret))
    assert r.status_code == 429 and r.json()["code"] == "mfa_locked"


async def test_daily_cap_blocks_even_when_short_window_is_free(env):
    from backend import mfa
    _, secret, _ = await _enable_mfa(env)
    u = await env.auth.get_user("alice")
    uid = u["id"]
    for _ in range(7):
        ch = await mfa.create_challenge(uid, u["token_version"])
        for _ in range(3):
            await mfa.claim_challenge(ch.token)
    r = await _mfa_login(env, await _challenge(env), _next_code(env, secret))
    assert r.status_code == 429 and r.json()["code"] == "mfa_locked"


async def test_backup_code_once(env):
    _, _, codes = await _enable_mfa(env)
    r = await _mfa_login(env, await _challenge(env), codes[0])
    assert r.status_code == 200
    r = await _mfa_login(env, await _challenge(env), codes[0])
    assert r.status_code == 401 and r.json()["code"] == "mfa_code_invalid"


async def test_challenge_rejected_after_logout_all(env):
    tok, secret, _ = await _enable_mfa(env)
    chal = await _challenge(env)
    assert (await env.c.post("/api/auth/logout-all", headers=_bearer(tok))).status_code == 200
    r = await _mfa_login(env, chal, _next_code(env, secret))
    assert r.status_code == 401 and r.json()["code"] == "mfa_challenge_invalid"


async def test_login_mfa_503_when_secret_unreadable(env):
    _, secret, _ = await _enable_mfa(env)
    async with aiosqlite.connect(env.path) as db:
        await db.execute("UPDATE users SET totp_secret='enc:garbage'")
        await db.commit()
    r = await _mfa_login(env, await _challenge(env), _next_code(env, secret))
    assert r.status_code == 503 and r.json()["code"] == "mfa_secret_unreadable"


async def test_login_mfa_validation_422(env):
    r = await env.c.post("/api/auth/login/mfa", json={"mfa_token": "", "code": ""})
    assert r.status_code == 422


# ── management endpoints ────────────────────────────────────────────────────

async def test_mfa_endpoints_require_auth(env):
    for method, path, body in (
        ("get", "/api/auth/mfa", None),
        ("post", "/api/auth/mfa/setup", {"current_password": PWD}),
        ("post", "/api/auth/mfa/enable", {"code": "123456"}),
        ("post", "/api/auth/mfa/disable", {"current_password": PWD, "code": "123456"}),
    ):
        r = await getattr(env.c, method)(path, **({"json": body} if body else {}))
        assert r.status_code == 401, path


async def test_state_endpoint(env):
    tok = (await _login(env)).json()["access_token"]
    r = await env.c.get("/api/auth/mfa", headers=_bearer(tok))
    assert r.json() == {"enabled": False, "enabled_at": None, "pending": False,
                        "backup_codes_remaining": 0, "encryption_available": True}
    tok, _, _ = await _enable_mfa(env)
    r = await env.c.get("/api/auth/mfa", headers=_bearer(tok))
    assert r.json()["enabled"] is True and r.json()["backup_codes_remaining"] == 10 and r.json()["enabled_at"]


async def test_setup_400_wrong_password(env):
    tok = (await _login(env)).json()["access_token"]
    r = await env.c.post("/api/auth/mfa/setup", json={"current_password": "wrongwrong"}, headers=_bearer(tok))
    assert r.status_code == 400 and r.json()["code"] == "invalid_password"


async def test_setup_no_store_header_and_payload(env):
    tok = (await _login(env)).json()["access_token"]
    r = await env.c.post("/api/auth/mfa/setup", json={"current_password": PWD}, headers=_bearer(tok))
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert set(body) == {"secret", "otpauth_uri", "qr_svg_data_uri"}
    assert body["otpauth_uri"].startswith("otpauth://totp/Hygie:alice?secret=" + body["secret"])


async def test_setup_409_when_already_enabled(env):
    tok, _, _ = await _enable_mfa(env)
    r = await env.c.post("/api/auth/mfa/setup", json={"current_password": PWD}, headers=_bearer(tok))
    assert r.status_code == 409 and r.json()["code"] == "mfa_already_enabled"


async def test_setup_409_without_encryption(env, monkeypatch):
    import backend.db.encryption as enc
    tok = (await _login(env)).json()["access_token"]
    monkeypatch.setattr(enc, "_get_fernet", lambda: None)
    r = await env.c.post("/api/auth/mfa/setup", json={"current_password": PWD}, headers=_bearer(tok))
    assert r.status_code == 409 and r.json()["code"] == "encryption_unavailable"


async def test_setup_password_rate_limited(env):
    tok = (await _login(env)).json()["access_token"]
    last = None
    for _ in range(7):
        last = await env.c.post("/api/auth/mfa/setup", json={"current_password": "wrongwrong"},
                                headers=_bearer(tok))
        if last.status_code == 429:
            break
    assert last.status_code == 429


async def test_enable_errors(env):
    tok = (await _login(env)).json()["access_token"]
    r = await env.c.post("/api/auth/mfa/enable", json={"code": "123456"}, headers=_bearer(tok))
    assert r.status_code == 400 and r.json()["code"] == "mfa_no_pending_setup"
    await env.c.post("/api/auth/mfa/setup", json={"current_password": PWD}, headers=_bearer(tok))
    r = await env.c.post("/api/auth/mfa/enable", json={"code": "000000"}, headers=_bearer(tok))
    assert r.status_code == 400 and r.json()["code"] == "mfa_code_invalid"


async def test_enable_409_when_already_enabled(env):
    tok, secret, _ = await _enable_mfa(env)
    r = await env.c.post("/api/auth/mfa/enable", json={"code": _next_code(env, secret)}, headers=_bearer(tok))
    assert r.status_code == 409 and r.json()["code"] == "mfa_already_enabled"


async def test_enable_revokes_old_refresh_cookie(env):
    r = await _login(env)
    old_cookie = r.cookies[COOKIE]
    tok = r.json()["access_token"]
    s = await env.c.post("/api/auth/mfa/setup", json={"current_password": PWD}, headers=_bearer(tok))
    code = totp.code_at(s.json()["secret"], int(env.clock[0]) // 30)
    r = await env.c.post("/api/auth/mfa/enable", json={"code": code}, headers=_bearer(tok))
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["enabled"] is True and len(body["backup_codes"]) == 10 and body["access_token"]
    new_cookie = r.cookies[COOKIE]
    assert new_cookie != old_cookie
    # old access token and old refresh cookie are dead; the new ones live
    assert (await env.c.get("/api/auth/me", headers=_bearer(tok))).status_code == 401
    env.c.cookies.clear()
    assert (await env.c.post("/api/auth/refresh", json={"refresh_token": old_cookie})).status_code == 401
    env.c.cookies.clear()
    assert (await env.c.post("/api/auth/refresh", json={"refresh_token": new_cookie})).status_code == 200


async def test_disable_requires_password_and_code(env):
    tok, secret, _ = await _enable_mfa(env)
    r = await env.c.post("/api/auth/mfa/disable", json={"current_password": "wrongwrong", "code": "000000"},
                         headers=_bearer(tok))
    assert r.status_code == 400 and r.json()["code"] == "invalid_password"
    r = await env.c.post("/api/auth/mfa/disable", json={"current_password": PWD, "code": "000000"},
                         headers=_bearer(tok))
    assert r.status_code == 400 and r.json()["code"] == "mfa_code_invalid"
    r = await env.c.post("/api/auth/mfa/disable",
                         json={"current_password": PWD, "code": _next_code(env, secret)}, headers=_bearer(tok))
    assert r.status_code == 200
    assert r.json()["enabled"] is False and r.json()["access_token"] and COOKIE in r.cookies
    assert (await env.c.get("/api/auth/me", headers=_bearer(tok))).status_code == 401
    env.c.cookies.clear()
    assert "access_token" in (await _login(env)).json()  # plain login again


async def test_disable_with_backup_code(env):
    tok, _, codes = await _enable_mfa(env)
    r = await env.c.post("/api/auth/mfa/disable", json={"current_password": PWD, "code": codes[3]},
                         headers=_bearer(tok))
    assert r.status_code == 200


async def test_disable_409_when_not_enabled(env):
    tok = (await _login(env)).json()["access_token"]
    r = await env.c.post("/api/auth/mfa/disable", json={"current_password": PWD, "code": "123456"},
                         headers=_bearer(tok))
    assert r.status_code == 409 and r.json()["code"] == "mfa_not_enabled"


# ── auth events ─────────────────────────────────────────────────────────────

def _kinds(events):
    return [(e["event"], e["method"], e["reason"]) for e in events]


async def test_auth_events_emitted_per_outcome(env, events):
    await _login(env, "wrongwrong")
    assert _kinds(events) == [("login_failure", "password", "invalid_credentials")]
    assert events[0]["username"] == "alice" and events[0]["ip"]
    events.clear()

    r = await _login(env)
    assert _kinds(events) == [("login_success", "password", "ok")] and events[0]["notify"] is True
    tok = r.json()["access_token"]
    events.clear()

    s = await env.c.post("/api/auth/mfa/setup", json={"current_password": PWD}, headers=_bearer(tok))
    secret = s.json()["secret"]
    assert events == []
    await env.c.post("/api/auth/mfa/enable", json={"code": "000000"}, headers=_bearer(tok))
    assert _kinds(events) == [("mfa_failure", "totp", "invalid_code")]
    events.clear()
    r = await env.c.post("/api/auth/mfa/enable",
                         json={"code": totp.code_at(secret, int(env.clock[0]) // 30)}, headers=_bearer(tok))
    assert _kinds(events) == [("mfa_enabled", "totp", "ok")]
    events.clear()

    chal = await _challenge(env)
    assert events == []  # password step alone is not a login
    await _mfa_login(env, chal, "000000")
    assert _kinds(events) == [("mfa_failure", "totp", "invalid_code")]
    events.clear()
    await _mfa_login(env, "nope", "123456")
    assert _kinds(events) == [("mfa_failure", "mfa", "challenge_invalid")]
    events.clear()
    r = await _mfa_login(env, chal, _next_code(env, secret))
    assert r.status_code == 200
    assert _kinds(events) == [("login_success", "totp", "ok")] and events[0]["notify"] is True
    events.clear()

    await _mfa_login(env, await _challenge(env), "aaaaaaaaaaaaaaaa")
    assert events[-1]["method"] == "backup_code" and events[-1]["reason"] == "invalid_code"
    events.clear()

    tok2 = r.json()["access_token"]
    await env.c.post("/api/auth/mfa/disable",
                     json={"current_password": PWD, "code": _next_code(env, secret)}, headers=_bearer(tok2))
    assert _kinds(events) == [("mfa_disabled", "totp", "ok")]


async def test_replay_event_reason(env, events):
    _, secret, _ = await _enable_mfa(env)
    code = _next_code(env, secret)
    await _mfa_login(env, await _challenge(env), code)
    events.clear()
    await _mfa_login(env, await _challenge(env), code)
    assert _kinds(events) == [("mfa_failure", "totp", "replay")]


async def test_locked_events(env, events):
    for _ in range(6):
        r = await _login(env, "wrongwrong")
    assert r.status_code == 429
    assert _kinds(events)[-1] == ("locked", "password", "rate_limit")


async def test_mfa_locked_event_reasons(env, events, monkeypatch):
    _, secret, _ = await _enable_mfa(env)
    for i in range(7):
        monkeypatch.setattr(env.router, "get_client_ip", lambda req, i=i: f"10.0.1.{i}")
        await _mfa_login(env, await _challenge(env), "000000")
    assert ("locked", "mfa", "rate_limit") in _kinds(events)


async def test_refresh_rejected_events(env, events):
    r = await _login(env)
    raw = r.cookies[COOKIE]
    await env.auth.revoke_all_refresh_tokens("alice")
    events.clear()
    env.c.cookies.clear()
    rr = await env.c.post("/api/auth/refresh", json={"refresh_token": raw})
    assert rr.status_code == 401
    assert _kinds(events) == [("refresh_rejected", "refresh", "revoked")]
    assert events[0]["username"] == "alice"
    events.clear()
    rr = await env.c.post("/api/auth/refresh", json={"refresh_token": "totally-unknown"})
    assert rr.status_code == 401
    assert events[0]["event"] == "refresh_rejected" and events[0]["username"] is None


async def test_refresh_version_event_when_session_revoked_midway(env, events, monkeypatch):
    r = await _login(env)
    raw = r.cookies[COOKIE]
    events.clear()

    async def none(*a, **k):
        return None
    monkeypatch.setattr(env.router, "create_refresh_token_if_version", none)
    env.c.cookies.clear()
    rr = await env.c.post("/api/auth/refresh", json={"refresh_token": raw})
    assert rr.status_code == 401
    assert _kinds(events) == [("refresh_rejected", "refresh", "version")]


async def test_refresh_locked_event(env, events):
    r = None
    for _ in range(7):
        r = await env.c.post("/api/auth/refresh", json={"refresh_token": "bogus"})
        if r.status_code == 429:
            break
    assert r.status_code == 429
    assert _kinds(events)[-1] == ("locked", "refresh", "rate_limit")


async def test_no_secret_or_code_in_events(env, events):
    _, secret, codes = await _enable_mfa(env)
    await _mfa_login(env, await _challenge(env), codes[0])
    blob = repr(events)
    assert secret not in blob and codes[0] not in blob and codes[0].replace("-", "") not in blob


# ── /mfa/disable and the 24 h failure cap ───────────────────────────────────

async def test_disable_wrong_code_counts_toward_daily_cap(env):
    from backend import mfa
    tok, _, _ = await _enable_mfa(env)
    uid = (await env.auth.get_user("alice"))["id"]
    before = await mfa.daily_failures(uid)
    r = await env.c.post("/api/auth/mfa/disable", json={"current_password": PWD, "code": "000000"},
                         headers=_bearer(tok))
    assert r.status_code == 400 and r.json()["code"] == "mfa_code_invalid"
    assert await mfa.daily_failures(uid) == before + 1


async def test_disable_blocked_by_daily_cap_even_with_valid_code(env, events):
    from backend import mfa
    tok, secret, _ = await _enable_mfa(env)
    uid = (await env.auth.get_user("alice"))["id"]
    for _ in range(mfa.DAILY_FAILURE_CAP):
        await mfa.record_code_failure(uid)
    r = await env.c.post("/api/auth/mfa/disable",
                         json={"current_password": PWD, "code": _next_code(env, secret)}, headers=_bearer(tok))
    assert r.status_code == 429 and r.json()["code"] == "mfa_locked"
    assert ("locked", "mfa", "daily_cap") in [(e["event"], e["method"], e["reason"]) for e in events]
    assert (await env.c.get("/api/auth/mfa", headers=_bearer(tok))).json()["enabled"] is True
