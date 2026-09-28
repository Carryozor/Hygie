"""Rate limiting must stay burst-safe under CONCURRENT requests.

Regression found in review of the check/record split (is_rate_limited() +
record_failure()): record_failure() only ran AFTER the slow Argon2 verify
step. A burst of N concurrent wrong-password requests could all call
is_rate_limited() and read the pre-record count (0 failures yet) before any
of them called record_failure() — every one of the N concurrent guesses got
to actually run verify_password(), instead of only RATE_LIMIT_MAX of them.
"5 attempts per window" silently became "N concurrent attempts per window".

Fix: rate_limit_attempt(key) atomically records-then-checks (like the
original combined rate_limit()) BEFORE the slow verification step, so the
(RATE_LIMIT_MAX + 1)th concurrent request to reach the DB is blocked
immediately, without ever calling verify_password(). release_attempt() then
un-records it on the success path only.
"""
import asyncio
import os
import threading

import pytest
import pytest_asyncio

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")


@pytest_asyncio.fixture
async def auth_client(tmp_path):
    """Minimal app with only the auth router — real sqlite-backed rate limiting,
    same pattern as test_rate_limit_check_vs_record.py's auth_client."""
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    from argon2 import PasswordHasher
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _ss

    import backend.auth as _auth_mod
    db_path = str(tmp_path / "auth_burst.db")
    _auth_mod.DB_PATH = db_path
    _auth_mod._rate_buckets.clear()
    _auth_mod._ph = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)

    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0

    from backend.db.schema import init_db
    await init_db()

    from backend.routers import auth as auth_router
    app = FastAPI()
    app.include_router(auth_router.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/api/auth/setup", json={"username": "admin", "password": "strongpass123"})
        yield c

    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    _auth_mod._rate_buckets.clear()


@pytest.mark.asyncio
async def test_concurrent_wrong_password_logins_never_exceed_rate_limit_max(auth_client):
    """Fire 10 concurrent wrong-password login requests from one IP.

    verify_password is patched to a slow (50ms), call-counting fake — wide
    enough to make the race window in the old check-then-record design
    reliably observable, and to prove the fix closes it: with atomic
    record-then-check on entry, the (MAX+1)th..10th concurrent requests must
    be blocked by rate_limit_attempt() BEFORE they ever reach
    verify_password(), so at most RATE_LIMIT_MAX calls happen no matter how
    many requests race in.
    """
    from backend.auth import RATE_LIMIT_MAX
    import backend.routers.auth as auth_router

    calls = {"n": 0}
    count_lock = threading.Lock()

    def _slow_wrong_password(password, password_hash):
        # Runs inside asyncio.to_thread — a real OS thread, so a plain
        # threading.Lock (not asyncio.Lock) is required here, and time.sleep
        # blocks only this worker thread, not the event loop.
        with count_lock:
            calls["n"] += 1
        import time as _time
        _time.sleep(0.05)
        return False

    auth_router.verify_password = _slow_wrong_password
    try:
        responses = await asyncio.gather(*[
            auth_client.post(
                "/api/auth/login",
                json={"username": "admin", "password": f"wrongpassword-{i}"},
            )
            for i in range(10)
        ])
    finally:
        from backend.auth import verify_password as _real_verify_password
        auth_router.verify_password = _real_verify_password

    assert len(responses) == 10
    statuses = [r.status_code for r in responses]
    assert 429 in statuses, f"expected at least one 429 among concurrent responses: {statuses}"
    assert calls["n"] <= RATE_LIMIT_MAX, (
        f"verify_password ran {calls['n']} times for {len(responses)} concurrent "
        f"requests — rate limiting must cap real password-verification attempts "
        f"at RATE_LIMIT_MAX ({RATE_LIMIT_MAX}), not let a burst bypass it"
    )
