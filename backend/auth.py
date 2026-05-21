"""
Authentication — JWT + Argon2id password hashing.

Public API:
  create_token(username) -> str
  verify_token(token) -> Optional[username]
  hash_password(pwd) -> str
  verify_password(pwd, hash) -> bool
  require_auth — FastAPI dependency
  rate_limit(key) -> bool   — True if rate limited
"""
import os
import time
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
import jwt as _pyjwt
from jwt.exceptions import InvalidTokenError as JWTError

from .database import DB_PATH

logger = logging.getLogger(__name__)

# ─── Secret key (auto-generated, persisted) ──────────────────────────────────
SECRET_FILE = os.path.join(os.path.dirname(DB_PATH), ".secret")


def _load_or_create_secret() -> str:
    """Load SECRET_KEY from data/.secret or env, else generate and persist."""
    env_key = os.environ.get("SECRET_KEY")
    if env_key and len(env_key) >= 32:
        return env_key

    try:
        if os.path.exists(SECRET_FILE):
            with open(SECRET_FILE, "r") as f:
                key = f.read().strip()
                if len(key) >= 32:
                    return key
    except Exception as e:
        logger.warning(f"Could not read secret file: {e}")

    # Generate & persist
    new_key = secrets.token_urlsafe(48)
    try:
        os.makedirs(os.path.dirname(SECRET_FILE), exist_ok=True)
        with open(SECRET_FILE, "w") as f:
            f.write(new_key)
        os.chmod(SECRET_FILE, 0o600)
        logger.info(f"Generated new SECRET_KEY at {SECRET_FILE}")
    except Exception as e:
        logger.warning(f"Could not persist secret: {e}")
    return new_key


SECRET_KEY = _load_or_create_secret()
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 7

_ph = PasswordHasher()
_bearer = HTTPBearer(auto_error=False)


# ─── Password hashing ─────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _ph.verify(password_hash, password)
        return True
    except VerifyMismatchError:
        return False
    except Exception:
        return False


# ─── JWT ──────────────────────────────────────────────────────────────────────
def create_token(username: str) -> str:
    payload = {
        "sub": username,
        "exp": datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS),
        "iat": datetime.now(timezone.utc),
    }
    return _pyjwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> Optional[str]:
    try:
        payload = _pyjwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


# ─── FastAPI dependency ───────────────────────────────────────────────────────
async def require_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> str:
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token requis"
        )
    username = verify_token(credentials.credentials)
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token invalide"
        )
    return username


# ─── User management ──────────────────────────────────────────────────────────
async def user_exists() -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            row = await cur.fetchone()
            return (row[0] or 0) > 0


async def create_user(username: str, password: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, hash_password(password), datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def get_user(username: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE username=?", (username,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def update_password(username: str, new_password: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET password_hash=? WHERE username=?",
            (hash_password(new_password), username),
        )
        await db.commit()


# ─── Rate limiting (in-memory, per-IP) ────────────────────────────────────────
_rate_buckets: dict = {}  # key -> list of timestamps

RATE_LIMIT_WINDOW = 300  # 5 minutes
RATE_LIMIT_MAX = 5  # 5 failed attempts


def rate_limit(key: str) -> bool:
    """Returns True if the key has exceeded the limit. Records this attempt."""
    now = time.time()
    bucket = _rate_buckets.get(key, [])
    # Trim expired entries
    bucket = [t for t in bucket if now - t < RATE_LIMIT_WINDOW]
    bucket.append(now)
    if bucket:
        _rate_buckets[key] = bucket
    elif key in _rate_buckets:
        del _rate_buckets[key]  # remove empty keys to prevent unbounded growth
    # Periodic full cleanup: drop all empty or expired-only buckets
    if len(_rate_buckets) > 10000:
        cutoff = now - RATE_LIMIT_WINDOW
        stale = [k for k, v in _rate_buckets.items() if not v or v[-1] < cutoff]
        for k in stale:
            del _rate_buckets[k]
    return len(bucket) > RATE_LIMIT_MAX


def get_client_ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
