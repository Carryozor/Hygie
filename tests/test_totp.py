"""RFC 6238 TOTP primitives (stdlib only)."""
import base64
from urllib.parse import urlsplit, parse_qs, unquote

import pytest

from backend import totp

RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


@pytest.mark.parametrize("t,expected", [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
])
def test_rfc6238_sha1_vectors(t, expected):
    assert totp.code_at(RFC_SECRET, t // 30, digits=8) == expected


def test_secret_160_bits():
    s = totp.generate_secret()
    assert len(s) == 32 and "=" not in s
    assert len(base64.b32decode(s)) == 20
    assert totp.generate_secret() != s


def test_verify_returns_matched_step():
    now = 1234567890
    code = totp.code_at(RFC_SECRET, now // 30)
    assert totp.verify(RFC_SECRET, code, now=now) == now // 30


def test_window_accepts_pm1_rejects_pm2():
    now = 1_700_000_000
    step = now // 30
    for delta in (-1, 0, 1):
        code = totp.code_at(RFC_SECRET, step + delta)
        assert totp.verify(RFC_SECRET, code, now=now) == step + delta
    for delta in (-2, 2):
        code = totp.code_at(RFC_SECRET, step + delta)
        assert totp.verify(RFC_SECRET, code, now=now) is None


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "12 456", None])
def test_verify_rejects_malformed(bad):
    assert totp.verify(RFC_SECRET, bad, now=59) is None


def test_otpauth_label_escaped():
    uri = totp.otpauth_uri("ABCDEFGH", "a b/c:d@é")
    parts = urlsplit(uri)
    assert parts.scheme == "otpauth" and parts.netloc == "totp"
    assert " " not in uri
    assert unquote(parts.path) == "/Hygie:a b/c:d@é"
    # the account name must not add path segments or extra colons
    assert parts.path.count("/") == 1 and parts.path.count(":") == 1
    q = parse_qs(parts.query)
    assert q["secret"] == ["ABCDEFGH"] and q["issuer"] == ["Hygie"]
    assert q["algorithm"] == ["SHA1"] and q["digits"] == ["6"] and q["period"] == ["30"]
