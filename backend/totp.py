"""RFC 6238 TOTP (HMAC-SHA1, 30 s step) in pure stdlib.

`verify` returns the matched time step rather than a bool: the caller needs it
to refuse a replay (a step may only ever be accepted once).
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1  # accept the previous, current and next step
SECRET_BYTES = 20  # 160 bits
ISSUER = "Hygie"


def generate_secret() -> str:
    """Random 160-bit secret, base32 without padding (32 characters)."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP_SECONDS)


def code_at(secret: str, step: int, digits: int = DIGITS) -> str:
    """HOTP(secret, step) truncated to `digits` digits (RFC 4226 / 6238)."""
    mac = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** digits)).zfill(digits)


def verify(secret: str, code: str | None, now: float | None = None) -> int | None:
    """Return the matched step within +/-WINDOW of `now`, else None.

    All candidate steps are compared in constant time without early exit.
    """
    if not isinstance(code, str) or len(code) != DIGITS or not code.isascii() or not code.isdigit():
        return None
    base = current_step(now)
    matched: int | None = None
    for step in range(base - WINDOW, base + WINDOW + 1):
        if hmac.compare_digest(code_at(secret, step), code):
            matched = step
    return matched


def otpauth_uri(secret: str, account: str, issuer: str = ISSUER) -> str:
    """otpauth:// provisioning URI with label and issuer percent-encoded."""
    # ':' separating issuer and account stays literal per the Key URI format;
    # an ':' inside the account is encoded (%3A) so the label has one separator.
    label = quote(issuer, safe="") + ":" + quote(account, safe="")
    query = urlencode(
        {"secret": secret, "issuer": issuer, "algorithm": "SHA1",
         "digits": DIGITS, "period": STEP_SECONDS},
        quote_via=quote,
    )
    return f"otpauth://totp/{label}?{query}"
