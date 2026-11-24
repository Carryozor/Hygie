"""MFA core logic (backend/mfa.py): setup, enable, TOTP + backup codes, challenges.

Every concurrency test fires N coroutines through asyncio.gather and asserts
EXACTLY ONE winner: that is what makes 2 uvicorn workers safe.
"""
import asyncio
import hashlib
import os
import time

import aiosqlite
import pytest
import pytest_asyncio

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")
os.environ.pop("DATABASE_URL", None)

from backend import mfa, totp  # noqa: E402


@pytest_asyncio.fixture
async def env(tmp_path):
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.auth as auth_mod

    db_path = str(tmp_path / "mfa.db")
    orig_engine, orig_utils = _eng.SQLITE_PATH, _db_utils.DB_PATH
    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    from backend.db.schema import init_db
    await init_db()
    await auth_mod.create_user("alice", "supersecret123")
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT id FROM users WHERE username='alice'") as cur:
            uid = (await cur.fetchone())[0]
    yield uid, db_path
    _eng.SQLITE_PATH = orig_engine
    _db_utils.DB_PATH = orig_utils


async def _q(path, sql, params=()):
    async with aiosqlite.connect(path) as db:
        async with db.execute(sql, params) as cur:
            return await cur.fetchall()


async def _enabled(env, now=None):
    """Run setup + enable; return (uid, secret, backup_codes)."""
    uid, _ = env
    setup = await mfa.begin_setup(uid, "alice")
    code = totp.code_at(setup.secret, int(now or time.time()) // 30)
    codes = await mfa.enable(uid, code)
    return uid, setup.secret, codes


# ── setup / enable ──────────────────────────────────────────────────────────

async def test_setup_stores_encrypted_pending_secret(env):
    uid, path = env
    setup = await mfa.begin_setup(uid, "alice")
    (raw, enabled), = await _q(path, "SELECT totp_secret, totp_enabled FROM users WHERE id=?", (uid,))
    assert raw.startswith("enc:") and setup.secret not in raw
    assert enabled == 0
    assert setup.otpauth_uri.startswith("otpauth://totp/Hygie:alice?secret=" + setup.secret)
    assert setup.qr_svg_data_uri.startswith("data:image/svg+xml;base64,")
    st = await mfa.get_state(uid)
    assert st.enabled is False and st.pending is True and st.encryption_available is True


async def test_setup_overwrites_pending_secret(env):
    uid, _ = env
    a = await mfa.begin_setup(uid, "alice")
    b = await mfa.begin_setup(uid, "alice")
    assert a.secret != b.secret
    with pytest.raises(mfa.MfaCodeInvalid):
        await mfa.enable(uid, totp.code_at(a.secret, int(time.time()) // 30))


async def test_setup_refused_without_fernet(env, monkeypatch):
    import backend.db.encryption as enc
    uid, path = env
    monkeypatch.setattr(enc, "_get_fernet", lambda: None)
    with pytest.raises(mfa.EncryptionUnavailable):
        await mfa.begin_setup(uid, "alice")
    assert (await _q(path, "SELECT totp_secret FROM users WHERE id=?", (uid,)))[0][0] is None
    assert (await mfa.get_state(uid)).encryption_available is False


async def test_enable_without_setup_raises(env):
    uid, _ = env
    with pytest.raises(mfa.MfaNoPendingSetup):
        await mfa.enable(uid, "123456")


async def test_enable_wrong_code_leaves_pending(env):
    uid, _ = env
    await mfa.begin_setup(uid, "alice")
    with pytest.raises(mfa.MfaCodeInvalid):
        await mfa.enable(uid, "000000")
    assert (await mfa.get_state(uid)).pending is True


async def test_enable_sets_last_step(env):
    uid, path = env
    now = int(time.time())
    await _enabled(env, now)
    (enabled, step, at), = await _q(
        path, "SELECT totp_enabled, totp_last_step, totp_enabled_at FROM users WHERE id=?", (uid,))
    assert enabled == 1 and step == now // 30 and at


async def test_enable_code_cannot_be_reused_to_login(env):
    uid, secret, _ = await _enabled(env)
    code = totp.code_at(secret, int(time.time()) // 30)
    res = await mfa.check_code(uid, code)
    assert res.ok is False and res.reason == "replay"


async def test_setup_and_enable_refused_when_already_enabled(env):
    uid, secret, _ = await _enabled(env)
    with pytest.raises(mfa.MfaAlreadyEnabled):
        await mfa.begin_setup(uid, "alice")
    with pytest.raises(mfa.MfaAlreadyEnabled):
        await mfa.enable(uid, totp.code_at(secret, int(time.time()) // 30 + 1))


async def test_backup_codes_only_sha256_in_db(env):
    uid, _, codes = await _enabled(env)
    _, path = env
    assert len(codes) == 10 and len(set(codes)) == 10
    for c in codes:
        parts = c.split("-")
        assert len(parts) == 4 and all(len(p) == 4 for p in parts)
    rows = await _q(path, "SELECT code_hash, used_at FROM mfa_backup_codes WHERE user_id=?", (uid,))
    hashes = {r[0] for r in rows}
    expected = {hashlib.sha256(c.replace("-", "").lower().encode()).hexdigest() for c in codes}
    assert hashes == expected and all(r[1] is None for r in rows)
    assert (await mfa.get_state(uid)).backup_codes_remaining == 10


async def test_concurrent_enable_only_one_wins(env):
    uid, _ = env
    setup = await mfa.begin_setup(uid, "alice")
    code = totp.code_at(setup.secret, int(time.time()) // 30)
    res = await asyncio.gather(*[mfa.enable(uid, code) for _ in range(5)], return_exceptions=True)
    wins = [r for r in res if isinstance(r, list)]
    assert len(wins) == 1
    _, path = env
    assert (await _q(path, "SELECT COUNT(*) FROM mfa_backup_codes WHERE user_id=?", (uid,)))[0][0] == 10


# ── TOTP verification ───────────────────────────────────────────────────────

async def test_totp_replay_rejected(env):
    uid, secret, _ = await _enabled(env)
    t = int(time.time()) // 30 + 1  # strictly after the enabling step
    code = totp.code_at(secret, t)
    first = await mfa.check_code(uid, code, now=t * 30)
    assert first.ok and first.method == "totp"
    second = await mfa.check_code(uid, code, now=t * 30)
    assert not second.ok and second.reason == "replay"


async def test_older_step_rejected_after_newer_accepted(env):
    uid, secret, _ = await _enabled(env)
    t = int(time.time()) // 30 + 1
    assert (await mfa.check_code(uid, totp.code_at(secret, t), now=t * 30)).ok
    older = await mfa.check_code(uid, totp.code_at(secret, t - 1), now=t * 30)
    assert not older.ok and older.reason == "replay"


async def test_wrong_totp_is_invalid_code(env):
    uid, _, _ = await _enabled(env)
    res = await mfa.check_code(uid, "000000")
    assert not res.ok and res.reason == "invalid_code" and res.method == "totp"


async def test_check_code_garbage_format(env):
    uid, _, _ = await _enabled(env)
    res = await mfa.check_code(uid, "not a code!")
    assert not res.ok and res.reason == "invalid_code"


async def test_check_code_when_mfa_not_enabled(env):
    uid, _ = env
    with pytest.raises(mfa.MfaNotEnabled):
        await mfa.check_code(uid, "123456")


async def test_same_code_concurrent_only_one_wins(env):
    uid, secret, _ = await _enabled(env)
    t = int(time.time()) // 30 + 1
    code = totp.code_at(secret, t)
    res = await asyncio.gather(*[mfa.check_code(uid, code, now=t * 30) for _ in range(8)])
    assert sum(1 for r in res if r.ok) == 1
    assert all(r.reason == "replay" for r in res if not r.ok)


async def test_unreadable_secret_raises(env):
    uid, path = env
    uid, _, codes = await _enabled(env)
    async with aiosqlite.connect(path) as db:
        await db.execute("UPDATE users SET totp_secret='enc:garbage' WHERE id=?", (uid,))
        await db.commit()
    with pytest.raises(mfa.MfaSecretUnreadable):
        await mfa.check_code(uid, "123456")
    # backup-code path never needs the secret
    assert (await mfa.check_code(uid, codes[0])).ok


# ── backup codes ────────────────────────────────────────────────────────────

async def test_backup_code_single_use_sequential(env):
    uid, _, codes = await _enabled(env)
    first = await mfa.check_code(uid, codes[0].upper())
    assert first.ok and first.method == "backup_code"
    again = await mfa.check_code(uid, codes[0])
    assert not again.ok and again.reason == "invalid_code" and again.method == "backup_code"
    assert (await mfa.get_state(uid)).backup_codes_remaining == 9


async def test_backup_code_accepts_spaces_and_no_dashes(env):
    uid, _, codes = await _enabled(env)
    assert (await mfa.check_code(uid, " " + codes[1].replace("-", " ") + " ")).ok


async def test_backup_code_single_use_concurrent(env):
    uid, _, codes = await _enabled(env)
    res = await asyncio.gather(*[mfa.check_code(uid, codes[2]) for _ in range(8)])
    assert sum(1 for r in res if r.ok) == 1
    assert (await mfa.get_state(uid)).backup_codes_remaining == 9


async def test_backup_code_of_other_user_rejected(env):
    import backend.auth as auth_mod
    uid, path = env
    _, _, codes = await _enabled(env)
    await auth_mod.create_user("bob", "supersecret456")
    bob = (await _q(path, "SELECT id FROM users WHERE username='bob'"))[0][0]
    # bob has MFA disabled: raises rather than leaking validity
    with pytest.raises(mfa.MfaNotEnabled):
        await mfa.check_code(bob, codes[0])


# ── challenges ──────────────────────────────────────────────────────────────

async def test_challenge_roundtrip_stores_only_hash(env):
    uid, path = env
    ch = await mfa.create_challenge(uid, 0)
    assert len(ch.token) >= 43 and ch.expires_in == 300
    (h,), = await _q(path, "SELECT token_hash FROM mfa_challenges")
    assert h == hashlib.sha256(ch.token.encode()).hexdigest() and ch.token not in h
    claimed = await mfa.claim_challenge(ch.token)
    assert claimed.user_id == uid and claimed.token_version == 0
    assert await mfa.consume_challenge(claimed.id) is True
    assert await mfa.consume_challenge(claimed.id) is False


async def test_challenge_capped_at_3(env):
    uid, _ = env
    ch = await mfa.create_challenge(uid, 0)
    for _ in range(3):
        await mfa.claim_challenge(ch.token)
    with pytest.raises(mfa.ChallengeInvalid):
        await mfa.claim_challenge(ch.token)


async def test_challenge_expired(env):
    uid, _ = env
    ch = await mfa.create_challenge(uid, 0, now=1000.0)
    with pytest.raises(mfa.ChallengeInvalid):
        await mfa.claim_challenge(ch.token, now=1000.0 + 301)
    assert (await mfa.claim_challenge(ch.token, now=1000.0 + 299)).user_id == uid


async def test_challenge_unknown_token(env):
    with pytest.raises(mfa.ChallengeInvalid):
        await mfa.claim_challenge("nope")


async def test_challenge_consumed_cannot_be_claimed(env):
    uid, _ = env
    ch = await mfa.create_challenge(uid, 0)
    c = await mfa.claim_challenge(ch.token)
    await mfa.consume_challenge(c.id)
    with pytest.raises(mfa.ChallengeInvalid):
        await mfa.claim_challenge(ch.token)


async def test_challenge_dead_after_password_change(env):
    import backend.auth as auth_mod
    uid, _ = env
    ch = await mfa.create_challenge(uid, 0)
    await auth_mod.invalidate_access_tokens("alice")  # bumps token_version
    with pytest.raises(mfa.ChallengeInvalid):
        await mfa.claim_challenge(ch.token)


async def test_challenge_claim_concurrent_respects_cap(env):
    uid, _ = env
    ch = await mfa.create_challenge(uid, 0)
    res = await asyncio.gather(*[mfa.claim_challenge(ch.token) for _ in range(10)], return_exceptions=True)
    assert sum(1 for r in res if isinstance(r, mfa.Challenge)) == 3
    assert sum(1 for r in res if isinstance(r, mfa.ChallengeInvalid)) == 7


async def test_challenge_consume_concurrent_only_one_wins(env):
    uid, _ = env
    ch = await mfa.create_challenge(uid, 0)
    c = await mfa.claim_challenge(ch.token)
    res = await asyncio.gather(*[mfa.consume_challenge(c.id) for _ in range(8)])
    assert res.count(True) == 1


async def test_old_challenges_purged_on_create(env):
    uid, path = env
    await mfa.create_challenge(uid, 0, now=1000.0)
    await mfa.create_challenge(uid, 0, now=1000.0 + 86400 + 10)
    assert (await _q(path, "SELECT COUNT(*) FROM mfa_challenges"))[0][0] == 1


async def test_daily_failure_cap(env):
    uid, _ = env
    assert await mfa.daily_failures(uid) == 0
    await mfa.assert_under_daily_cap(uid)
    # 7 challenges x 3 claimed attempts = 21 failures >= cap of 20
    for _ in range(7):
        ch = await mfa.create_challenge(uid, 0)
        for _ in range(3):
            await mfa.claim_challenge(ch.token)
    assert await mfa.daily_failures(uid) == 21
    with pytest.raises(mfa.MfaDailyCapReached):
        await mfa.assert_under_daily_cap(uid)


async def test_successful_login_does_not_count_as_failure(env):
    uid, _ = env
    ch = await mfa.create_challenge(uid, 0)
    c = await mfa.claim_challenge(ch.token)
    await mfa.consume_challenge(c.id)
    assert await mfa.daily_failures(uid) == 0


async def test_daily_cap_window_is_24h(env):
    uid, _ = env
    old = time.time() - 86400 - 60
    ch = await mfa.create_challenge(uid, 0, now=old)
    for _ in range(3):
        await mfa.claim_challenge(ch.token, now=old)
    assert await mfa.daily_failures(uid) == 0


# ── disable / state ─────────────────────────────────────────────────────────

async def test_disable_clears_everything(env):
    uid, path = env
    await _enabled(env)
    await mfa.create_challenge(uid, 0)
    await mfa.disable(uid)
    (sec, en, step, at), = await _q(
        path, "SELECT totp_secret, totp_enabled, totp_last_step, totp_enabled_at FROM users WHERE id=?", (uid,))
    assert (sec, en, step, at) == (None, 0, None, None)
    assert (await _q(path, "SELECT COUNT(*) FROM mfa_backup_codes"))[0][0] == 0
    assert (await _q(path, "SELECT COUNT(*) FROM mfa_challenges"))[0][0] == 0
    st = await mfa.get_state(uid)
    assert not st.enabled and not st.pending and st.backup_codes_remaining == 0


async def test_disable_when_not_enabled_raises(env):
    uid, _ = env
    with pytest.raises(mfa.MfaNotEnabled):
        await mfa.disable(uid)


async def test_state_enabled(env):
    uid, _, _ = await _enabled(env)
    st = await mfa.get_state(uid)
    assert st.enabled and st.enabled_at and not st.pending and st.backup_codes_remaining == 10


async def test_remove_user_cascades_mfa_rows(env):
    uid, path = env
    await _enabled(env)
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("DELETE FROM users WHERE id=?", (uid,))
        await db.commit()
    assert (await _q(path, "SELECT COUNT(*) FROM mfa_backup_codes"))[0][0] == 0
