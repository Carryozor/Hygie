"""TOTP two-factor authentication: business logic (no HTTP).

All shared state lives in the DB and every transition is a conditional UPDATE
whose rowcount decides the single winner, so two uvicorn workers (CLAUDE.md
trap 5) cannot both accept the same TOTP step, backup code or challenge.
Every such UPDATE changes a value (NULL -> x, n -> n+1, step -> larger step):
MariaDB reports *changed* rows, not *matched* rows.

Routes (phase B) own: rate limiting, password checks, session revocation,
auth events. This module owns: secrets, codes, challenges, state.
"""
import base64
import hashlib
import hmac
import io
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import segno

from . import totp
from .db import encryption as _enc
from .db.engine import get_db

CHALLENGE_TTL_SECONDS = 300
CHALLENGE_MAX_ATTEMPTS = 3
DAILY_FAILURE_CAP = 20
DAILY_WINDOW_SECONDS = 86400
BACKUP_CODE_COUNT = 10
_BACKUP_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
_BACKUP_LEN = 16
_TOTP_RE = re.compile(r"^\d{6}$")
_BACKUP_RE = re.compile(r"^[a-z2-7]{16}$")


# ── exceptions ──────────────────────────────────────────────────────────────

class MfaError(Exception):
    """Base class for every typed MFA failure."""


class MfaAlreadyEnabled(MfaError):
    pass


class MfaNotEnabled(MfaError):
    pass


class MfaNoPendingSetup(MfaError):
    pass


class MfaCodeInvalid(MfaError):
    """Wrong or replayed confirmation code during enable()."""


class EncryptionUnavailable(MfaError):
    """No Fernet key: refusing to store a TOTP secret in clear text."""


class MfaSecretUnreadable(MfaError):
    """Stored secret cannot be decrypted (key lost / rotated / corrupted)."""


class ChallengeInvalid(MfaError):
    """Unknown, expired, consumed, exhausted or stale (token_version) challenge."""


class MfaDailyCapReached(MfaError):
    pass


# ── value objects ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SetupInfo:
    secret: str
    otpauth_uri: str
    qr_svg_data_uri: str


@dataclass(frozen=True)
class MfaState:
    enabled: bool
    enabled_at: str | None
    pending: bool
    backup_codes_remaining: int
    encryption_available: bool


@dataclass(frozen=True)
class CodeCheck:
    """Outcome of check_code. method: totp | backup_code.
    reason: ok | invalid_code | replay."""
    ok: bool
    method: str
    reason: str


@dataclass(frozen=True)
class NewChallenge:
    token: str
    expires_in: int


@dataclass(frozen=True)
class Challenge:
    id: int
    user_id: int
    token_version: int


# ── helpers ─────────────────────────────────────────────────────────────────

def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_code(raw: str | None) -> str:
    """Lower-case and strip whitespace and dashes (`ABCD-efgh 12` -> `abcdefgh12`)."""
    return re.sub(r"[\s-]+", "", raw or "").lower()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_backup_codes() -> list[str]:
    codes = []
    for _ in range(BACKUP_CODE_COUNT):
        raw = "".join(secrets.choice(_BACKUP_ALPHABET) for _ in range(_BACKUP_LEN))
        codes.append("-".join(raw[i:i + 4] for i in range(0, _BACKUP_LEN, 4)))
    return codes


def _qr_data_uri(text: str) -> str:
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=6, border=2, xmldecl=False, nl=False)
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()


def _read_secret(stored: str | None) -> str:
    """Decrypt a stored secret or raise MfaSecretUnreadable.

    _decrypt_value() returns the raw value on failure, so success is proven by
    the prefix being gone and the result being valid base32.
    """
    if not stored or not stored.startswith(_enc._ENC_PREFIX):
        raise MfaSecretUnreadable("TOTP secret is not stored encrypted")
    plain = _enc._decrypt_value(stored)
    if plain.startswith(_enc._ENC_PREFIX):
        raise MfaSecretUnreadable("TOTP secret cannot be decrypted")
    try:
        totp.code_at(plain, 0)
    except ValueError as e:  # binascii.Error is a ValueError
        raise MfaSecretUnreadable("TOTP secret is corrupted") from e
    return plain


async def _user_row(db, user_id: int) -> dict | None:
    return await db.fetch_one(
        "SELECT id, totp_secret, totp_enabled, totp_last_step, totp_enabled_at FROM users WHERE id=?",
        (user_id,),
    )


# ── state / setup / enable / disable ────────────────────────────────────────

async def get_state(user_id: int) -> MfaState:
    async with get_db() as db:
        row = await _user_row(db, user_id)
        remaining = await db.fetch_one(
            "SELECT COUNT(*) AS n FROM mfa_backup_codes WHERE user_id=? AND used_at IS NULL", (user_id,))
    enabled = bool(row and row["totp_enabled"])
    return MfaState(
        enabled=enabled,
        enabled_at=row["totp_enabled_at"] if enabled else None,
        pending=bool(row and not enabled and row["totp_secret"]),
        backup_codes_remaining=int(remaining["n"]) if remaining else 0,
        encryption_available=_enc._get_fernet() is not None,
    )


async def begin_setup(user_id: int, username: str) -> SetupInfo:
    """Store a fresh encrypted pending secret (overwrites a previous pending one)."""
    if _enc._get_fernet() is None:
        raise EncryptionUnavailable("no encryption key available")
    secret = totp.generate_secret()
    uri = totp.otpauth_uri(secret, username)
    async with get_db() as db:
        changed = await db.execute_write(
            "UPDATE users SET totp_secret=? WHERE id=? AND totp_enabled=0",
            (_enc._encrypt_value(secret), user_id),
        )
        if changed != 1:
            raise MfaAlreadyEnabled("2FA is already enabled")
        await db.commit()
    return SetupInfo(secret=secret, otpauth_uri=uri, qr_svg_data_uri=_qr_data_uri(uri))


async def enable(user_id: int, code: str, now: float | None = None) -> list[str]:
    """Confirm the pending secret with a TOTP code; returns the 10 backup codes (shown once)."""
    async with get_db() as db:
        row = await _user_row(db, user_id)
        if row and row["totp_enabled"]:
            raise MfaAlreadyEnabled("2FA is already enabled")
        if not row or not row["totp_secret"]:
            raise MfaNoPendingSetup("no pending setup")
        stored = row["totp_secret"]
        step = totp.verify(_read_secret(stored), normalize_code(code), now=now)
        if step is None:
            raise MfaCodeInvalid("invalid confirmation code")
        won = await db.execute_write(
            "UPDATE users SET totp_enabled=1, totp_last_step=?, totp_enabled_at=? "
            "WHERE id=? AND totp_enabled=0 AND totp_secret=?",
            (step, _iso_now(), user_id, stored),
        )
        if won != 1:
            raise MfaAlreadyEnabled("2FA was enabled concurrently")
        codes = _generate_backup_codes()
        await db.execute_write("DELETE FROM mfa_backup_codes WHERE user_id=?", (user_id,))
        created = _iso_now()
        for c in codes:
            await db.execute(
                "INSERT INTO mfa_backup_codes (user_id, code_hash, created_at) VALUES (?, ?, ?)",
                (user_id, _sha256(normalize_code(c)), created),
            )
        await db.commit()
    return codes


async def disable(user_id: int) -> None:
    """Turn 2FA off and drop secret, backup codes and challenges.

    The caller revokes the user's sessions."""
    async with get_db() as db:
        won = await db.execute_write(
            "UPDATE users SET totp_secret=NULL, totp_enabled=0, totp_last_step=NULL, totp_enabled_at=NULL "
            "WHERE id=? AND totp_enabled=1",
            (user_id,),
        )
        if won != 1:
            raise MfaNotEnabled("2FA is not enabled")
        await db.execute_write("DELETE FROM mfa_backup_codes WHERE user_id=?", (user_id,))
        await db.execute_write("DELETE FROM mfa_challenges WHERE user_id=?", (user_id,))
        await db.commit()


# ── code verification ───────────────────────────────────────────────────────

async def check_code(user_id: int, raw_code: str, now: float | None = None) -> CodeCheck:
    """Verify a TOTP code (6 digits) or a backup code (16 base32 chars).

    Raises MfaNotEnabled / MfaSecretUnreadable (TOTP path only). A format that
    is neither yields invalid_code. Rate limiting is the caller's job."""
    code = normalize_code(raw_code)
    async with get_db() as db:
        row = await _user_row(db, user_id)
        if not row or not row["totp_enabled"]:
            raise MfaNotEnabled("2FA is not enabled")
        if _BACKUP_RE.match(code):
            return await _check_backup(db, user_id, code)
        if not _TOTP_RE.match(code):
            return CodeCheck(False, "totp", "invalid_code")
        step = totp.verify(_read_secret(row["totp_secret"]), code, now=now)
        if step is None:
            return CodeCheck(False, "totp", "invalid_code")
        won = await db.execute_write(
            "UPDATE users SET totp_last_step=? WHERE id=? "
            "AND (totp_last_step IS NULL OR totp_last_step<?)",
            (step, user_id, step),
        )
        await db.commit()
    return CodeCheck(True, "totp", "ok") if won == 1 else CodeCheck(False, "totp", "replay")


async def _check_backup(db, user_id: int, code: str) -> CodeCheck:
    digest = _sha256(code)
    rows = await db.fetch_all(
        "SELECT id, code_hash FROM mfa_backup_codes WHERE user_id=? AND used_at IS NULL", (user_id,))
    match_id = None
    for r in rows:  # no early exit: constant work whatever the position
        if hmac.compare_digest(r["code_hash"], digest):
            match_id = r["id"]
    if match_id is None:
        return CodeCheck(False, "backup_code", "invalid_code")
    won = await db.execute_write(
        "UPDATE mfa_backup_codes SET used_at=? WHERE id=? AND used_at IS NULL",
        (_iso_now(), match_id),
    )
    await db.commit()
    return CodeCheck(True, "backup_code", "ok") if won == 1 else CodeCheck(False, "backup_code", "invalid_code")


# ── login challenges ────────────────────────────────────────────────────────

async def create_challenge(user_id: int, token_version: int, now: float | None = None) -> NewChallenge:
    """Opaque 5-minute challenge issued after a correct password. Only its SHA-256 is stored."""
    t = time.time() if now is None else now
    raw = secrets.token_urlsafe(32)
    async with get_db() as db:
        await db.execute_write(
            "DELETE FROM mfa_challenges WHERE created_at<?", (t - DAILY_WINDOW_SECONDS,))
        await db.execute(
            "INSERT INTO mfa_challenges (user_id, token_hash, token_version, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, _sha256(raw), token_version, t, t + CHALLENGE_TTL_SECONDS),
        )
        await db.commit()
    return NewChallenge(token=raw, expires_in=CHALLENGE_TTL_SECONDS)


async def claim_challenge(raw_token: str, now: float | None = None) -> Challenge:
    """Atomically spend one attempt (max 3) on a live challenge.

    Raises ChallengeInvalid if unknown, expired, consumed, out of attempts or if
    the user's token_version moved on (password change, logout-all)."""
    t = time.time() if now is None else now
    digest = _sha256(raw_token or "")
    async with get_db() as db:
        won = await db.execute_write(
            "UPDATE mfa_challenges SET attempts=attempts+1 WHERE token_hash=? "
            "AND consumed_at IS NULL AND attempts<? AND expires_at>?",
            (digest, CHALLENGE_MAX_ATTEMPTS, t),
        )
        await db.commit()  # the spent attempt must persist even if we raise below
        if won != 1:
            raise ChallengeInvalid("challenge not claimable")
        row = await db.fetch_one(
            "SELECT c.id, c.user_id, c.token_version, u.token_version AS current_version "
            "FROM mfa_challenges c JOIN users u ON u.id = c.user_id WHERE c.token_hash=?",
            (digest,),
        )
    if not row or (row["current_version"] or 0) != row["token_version"]:
        raise ChallengeInvalid("challenge is stale")
    return Challenge(id=row["id"], user_id=row["user_id"], token_version=row["token_version"])


async def consume_challenge(challenge_id: int, now: float | None = None) -> bool:
    """Mark a claimed challenge as spent. True for exactly one caller."""
    t = time.time() if now is None else now
    async with get_db() as db:
        won = await db.execute_write(
            "UPDATE mfa_challenges SET consumed_at=? WHERE id=? AND consumed_at IS NULL",
            (t, challenge_id),
        )
        await db.commit()
    return won == 1


async def daily_failures(user_id: int, now: float | None = None) -> int:
    """Failed code attempts in the last 24 h: spent attempts minus successful (consumed) logins."""
    t = time.time() if now is None else now
    async with get_db() as db:
        row = await db.fetch_one(
            "SELECT COALESCE(SUM(attempts), 0) AS spent, COUNT(consumed_at) AS ok "
            "FROM mfa_challenges WHERE user_id=? AND created_at>?",
            (user_id, t - DAILY_WINDOW_SECONDS),
        )
    return max(0, int(row["spent"]) - int(row["ok"])) if row else 0


async def record_code_failure(user_id: int, now: float | None = None) -> None:
    """Count one failed code attempt made outside a login challenge (e.g. /mfa/disable).

    Inserts an already-expired, never-claimable challenge row (random discarded
    token) with attempts=1: it feeds daily_failures() and nothing else."""
    t = time.time() if now is None else now
    async with get_db() as db:
        row = await db.fetch_one("SELECT token_version FROM users WHERE id=?", (user_id,))
        await db.execute(
            "INSERT INTO mfa_challenges (user_id, token_hash, token_version, created_at, expires_at, attempts) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (user_id, _sha256(secrets.token_urlsafe(32)), (row or {}).get("token_version") or 0, t, t),
        )
        await db.commit()


async def assert_under_daily_cap(user_id: int, now: float | None = None) -> None:
    """Raise MfaDailyCapReached at DAILY_FAILURE_CAP failures / 24 h / account.

    Not atomic by itself; the caller's per-account 5 / 5 min limiter bounds the overshoot."""
    if await daily_failures(user_id, now) >= DAILY_FAILURE_CAP:
        raise MfaDailyCapReached("daily MFA failure cap reached")
