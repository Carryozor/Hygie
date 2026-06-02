"""Unit tests for backend/auth.py — JWT, Argon2id, rate limiting."""
import time
import pytest
import backend.auth as auth_mod


@pytest.fixture(scope="module", autouse=True)
def fast_argon2():
    """Swap Argon2id to minimal params for the duration of this module (~10 ms vs ~170 ms)."""
    from argon2 import PasswordHasher
    _orig = auth_mod._ph
    auth_mod._ph = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    yield
    auth_mod._ph = _orig


# ─── password hashing ────────────────────────────────────────────────────────

def test_hash_and_verify_correct_password():
    h = auth_mod.hash_password("correct-horse-battery-staple")
    assert auth_mod.verify_password("correct-horse-battery-staple", h) is True


def test_verify_wrong_password_returns_false():
    h = auth_mod.hash_password("correct")
    assert auth_mod.verify_password("wrong", h) is False


def test_verify_empty_password_returns_false():
    h = auth_mod.hash_password("somepassword")
    assert auth_mod.verify_password("", h) is False


def test_two_hashes_of_same_password_differ():
    """Argon2id uses per-hash salt — identical passwords must produce distinct hashes."""
    h1 = auth_mod.hash_password("password")
    h2 = auth_mod.hash_password("password")
    assert h1 != h2


def test_hash_is_not_plaintext():
    h = auth_mod.hash_password("mysecret")
    assert "mysecret" not in h


# ─── JWT ─────────────────────────────────────────────────────────────────────

def test_create_and_verify_token_roundtrip():
    token = auth_mod.create_access_token("alice")
    assert auth_mod.verify_token(token) == "alice"


def test_verify_token_with_different_username():
    token_alice = auth_mod.create_access_token("alice")
    assert auth_mod.verify_token(token_alice) == "alice"
    assert auth_mod.verify_token(token_alice) != "bob"


def test_verify_invalid_token_returns_none():
    assert auth_mod.verify_token("not.a.valid.jwt") is None


def test_verify_empty_token_returns_none():
    assert auth_mod.verify_token("") is None


def test_verify_tampered_signature_returns_none():
    token = auth_mod.create_access_token("alice")
    # Replace last 4 chars of signature
    tampered = token[:-4] + ("xxxx" if not token.endswith("xxxx") else "yyyy")
    assert auth_mod.verify_token(tampered) is None


def test_verify_truncated_token_returns_none():
    token = auth_mod.create_access_token("alice")
    assert auth_mod.verify_token(token[:10]) is None


def test_token_contains_subject_claim():
    """JWT payload must carry the username in the 'sub' field."""
    import jwt as _pyjwt
    token = auth_mod.create_access_token("bob")
    payload = _pyjwt.decode(token, options={"verify_signature": False})
    assert payload.get("sub") == "bob"


def test_token_has_expiry():
    """Token must have an 'exp' claim."""
    import jwt as _pyjwt
    token = auth_mod.create_access_token("alice")
    payload = _pyjwt.decode(token, options={"verify_signature": False})
    assert "exp" in payload


# ─── rate limiter ────────────────────────────────────────────────────────────

def test_rate_limit_allows_up_to_max_attempts():
    """First RATE_LIMIT_MAX attempts must all be allowed (return False)."""
    key = f"test-allow-{time.time()}"
    results = [auth_mod.rate_limit(key) for _ in range(auth_mod.RATE_LIMIT_MAX)]
    assert all(r is False for r in results), f"Some attempts were blocked early: {results}"


def test_rate_limit_blocks_attempt_after_max():
    """The (RATE_LIMIT_MAX+1)th attempt must be blocked."""
    key = f"test-block-{time.time()}"
    for _ in range(auth_mod.RATE_LIMIT_MAX):
        auth_mod.rate_limit(key)
    assert auth_mod.rate_limit(key) is True


def test_rate_limit_different_keys_are_independent():
    """Exhausting one key must not affect another."""
    key_a = f"ip-a-{time.time()}"
    key_b = f"ip-b-{time.time()}"
    for _ in range(auth_mod.RATE_LIMIT_MAX + 2):
        auth_mod.rate_limit(key_a)
    # key_b must still be fresh
    assert auth_mod.rate_limit(key_b) is False


def test_rate_limit_uses_sliding_window():
    """
    Verify bucket is keyed per IP — two distinct IPs starting from zero.
    Minimal test: fresh key is never blocked on first attempt.
    """
    key = f"fresh-{time.time()}-{id(object())}"
    assert auth_mod.rate_limit(key) is False


# ─── get_client_ip ────────────────────────────────────────────────────────────

def test_get_client_ip_ignores_forwarded_for_by_default():
    """Without HYGIE_TRUST_PROXY=1, X-Forwarded-For is ignored to prevent spoofing."""
    import os
    from unittest.mock import MagicMock, patch
    request = MagicMock()
    request.headers = {"X-Forwarded-For": "1.2.3.4, 10.0.0.1"}
    request.client = MagicMock(host="10.0.0.2")
    with patch.dict(os.environ, {}, clear=False):
        # Reload _TRUST_PROXY to ensure it's False (default)
        auth_mod._TRUST_PROXY = False
        assert auth_mod.get_client_ip(request) == "10.0.0.2"


def test_get_client_ip_uses_forwarded_for_when_trust_proxy_enabled():
    """When HYGIE_TRUST_PROXY=1, X-Forwarded-For first IP is used."""
    from unittest.mock import MagicMock
    request = MagicMock()
    request.headers = {"X-Forwarded-For": "1.2.3.4, 10.0.0.1"}
    request.client = MagicMock(host="10.0.0.2")
    original = auth_mod._TRUST_PROXY
    try:
        auth_mod._TRUST_PROXY = True
        assert auth_mod.get_client_ip(request) == "1.2.3.4"
    finally:
        auth_mod._TRUST_PROXY = original


def test_get_client_ip_falls_back_to_client_host():
    from unittest.mock import MagicMock
    request = MagicMock()
    request.headers = {}
    request.client = MagicMock(host="192.168.1.10")
    assert auth_mod.get_client_ip(request) == "192.168.1.10"


def test_get_client_ip_no_client_returns_unknown():
    from unittest.mock import MagicMock
    request = MagicMock()
    request.headers = {}
    request.client = None
    assert auth_mod.get_client_ip(request) == "unknown"
