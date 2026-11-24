"""
Authentication — JWT + Argon2id password hashing.

Public API:
  create_access_token(username) -> str
  verify_token(token) -> Optional[username]
  hash_password(pwd) -> str
  verify_password(pwd, hash) -> bool
  require_auth — FastAPI dependency
  rate_limit(key) -> bool                  — True if rate limited; records every call (setup only)
  rate_limit_attempt(key) -> (bool, token) — atomically records+checks; burst-safe (see docstring)
  release_attempt(key, token) -> None      — un-records an attempt on the SUCCESS path
  get_client_ip(request) -> str
"""
import asyncio
import hashlib
import ipaddress
import os
import time
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
import jwt as _pyjwt
from jwt.exceptions import InvalidTokenError as JWTError

from .db.utils import DB_PATH
from .db.engine import get_db

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
ACCESS_TOKEN_EXPIRE_MINUTES = 60   # 1 hour
REFRESH_TOKEN_EXPIRE_DAYS   = 30   # 30 days
REFRESH_ROTATION_GRACE_SECONDS = 60   # an already-rotated token stays usable this long (concurrent tabs)

# Whether to trust the X-Forwarded-For header for rate limiting.
# Only enable when a trusted reverse proxy is guaranteed to be in front.
# Set HYGIE_TRUST_PROXY=1 in that case; default is off (use direct client IP).
_TRUST_PROXY = os.environ.get("HYGIE_TRUST_PROXY", "0").strip() in ("1", "true", "yes")

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
    except Exception as e:
        logger.debug("verify_password unexpected error: %s", e)
        return False


# ─── JWT ──────────────────────────────────────────────────────────────────────
def create_access_token(username: str, version: int = 0) -> str:
    """Create a short-lived JWT access token (1 hour).

    `version` is the user's token_version at issue time; bumping it in the DB
    (password change, logout-all, refresh reuse) kills every older token.
    """
    payload = {
        "sub":  username,
        "ver":  version,
        "type": "access",
        "exp":  datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat":  datetime.now(timezone.utc),
    }
    return _pyjwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> Optional[str]:
    """Verify an access token. Returns username or None."""
    try:
        payload = _pyjwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        token_type = payload.get("type")
        if token_type and token_type != "access":
            return None
        return payload.get("sub")
    except JWTError:
        return None


# ─── Refresh token ────────────────────────────────────────────────────────────
def _hash_token(token: str) -> str:
    """SHA-256 hash for DB storage — never store raw tokens."""
    return hashlib.sha256(token.encode()).hexdigest()


async def create_refresh_token(username: str) -> str:
    """Create a long-lived refresh token (30 days), stored hashed in DB."""
    raw    = secrets.token_urlsafe(32)
    hashed = _hash_token(raw)
    expires = (datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()

    async with get_db() as db:
        user = await db.fetch_one("SELECT id FROM users WHERE username=?", (username,))
        if not user:
            raise ValueError(f"User {username!r} not found")
        created = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO refresh_tokens (user_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (user["id"], hashed, expires, created),
        )
        await db.commit()
    return raw


async def create_refresh_token_if_version(username: str, expected_version: int) -> Optional[str]:
    """Create a refresh token only if the user's token_version is still `expected_version`.

    The first statement is a no-op UPDATE on the user row: it takes the row lock
    (portable across SQLite/MariaDB, no FOR UPDATE) BEFORE any read, so the
    version read below is taken after the lock and cannot be stale. A concurrent
    revoke_all_refresh_tokens (which bumps the version first) either commits
    before us (we see the new version, return None) or waits for us (and then
    revokes the row we inserted). Returns None when the session was revoked.
    """
    raw    = secrets.token_urlsafe(32)
    hashed = _hash_token(raw)
    expires = (datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()

    async with get_db() as db:
        await db.execute("UPDATE users SET token_version = token_version WHERE username=?", (username,))
        user = await db.fetch_one("SELECT id, token_version FROM users WHERE username=?", (username,))
        if not user or (user["token_version"] or 0) != expected_version:
            return None
        created = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO refresh_tokens (user_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (user["id"], hashed, expires, created),
        )
        await db.commit()
    return raw


async def verify_refresh_token(raw: str) -> Optional[tuple[str, int]]:
    """Returns (username, token_version) if token is valid, not expired, not revoked. Else None.

    The version is read in the same row snapshot as the token itself.

    A token that was already rotated and is presented again after its grace
    window has been replayed (stolen cookie, or a client that kept an old one):
    every session of that user is revoked, not just this token.
    """
    hashed = _hash_token(raw)
    async with get_db() as db:
        row = await db.fetch_one(
            """SELECT u.username, u.token_version, rt.expires_at, rt.revoked, rt.rotated_at
               FROM refresh_tokens rt
               JOIN users u ON u.id = rt.user_id
               WHERE rt.token_hash = ?""",
            (hashed,),
        )
    if not row or row["revoked"]:
        return None
    if row["rotated_at"] and _past_rotation_grace(row["rotated_at"]):
        logger.warning("Refresh token reuse detected for %r — revoking all sessions", row["username"])
        await revoke_all_refresh_tokens(row["username"])
        return None
    expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) > expires:
        return None
    return row["username"], (row["token_version"] or 0)


def _past_rotation_grace(rotated_at: str) -> bool:
    try:
        when = datetime.fromisoformat(rotated_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(timezone.utc) > when + timedelta(seconds=REFRESH_ROTATION_GRACE_SECONDS)


async def retire_refresh_token(raw: str, grace_seconds: int = REFRESH_ROTATION_GRACE_SECONDS) -> None:
    """Shorten a refresh token's lifetime to a small grace window.

    Used for rotation: the old token must survive a few seconds so that
    concurrent refreshes (multiple tabs sharing the cookie) don't log the
    user out, but it must not remain valid for its full 30 days.
    """
    hashed = _hash_token(raw)
    now = datetime.now(timezone.utc)
    cutoff = (now + timedelta(seconds=grace_seconds)).isoformat()
    async with get_db() as db:
        await db.execute(
            "UPDATE refresh_tokens SET expires_at=?, rotated_at=COALESCE(rotated_at, ?) "
            "WHERE token_hash=? AND expires_at > ?",
            (cutoff, now.isoformat(), hashed, cutoff),
        )
        await db.commit()


async def revoke_refresh_token(raw: str) -> None:
    """Mark a specific refresh token as revoked."""
    hashed = _hash_token(raw)
    async with get_db() as db:
        await db.execute(
            "UPDATE refresh_tokens SET revoked=1 WHERE token_hash=?", (hashed,)
        )
        await db.commit()


async def revoke_all_refresh_tokens(username: str) -> None:
    """Revoke all active refresh tokens AND outstanding access tokens of a user.

    One transaction, lock order users -> refresh_tokens: the version bump comes
    first, so a concurrent create_refresh_token_if_version either sees it or is
    waited for (and its freshly inserted row is revoked by the second UPDATE).
    """
    async with get_db() as db:
        await db.execute("UPDATE users SET token_version = token_version + 1 WHERE username=?", (username,))
        await db.execute(
            "UPDATE refresh_tokens SET revoked=1 WHERE revoked=0 "
            "AND user_id IN (SELECT id FROM users WHERE username=?)",
            (username,),
        )
        await db.commit()


async def invalidate_access_tokens(username: str) -> None:
    """Reject every access token issued so far for this user."""
    async with get_db() as db:
        await db.execute("UPDATE users SET token_version = token_version + 1 WHERE username=?", (username,))
        await db.commit()


async def current_token_version(username: str) -> int:
    async with get_db() as db:
        row = await db.fetch_one("SELECT token_version FROM users WHERE username=?", (username,))
    return ((row["token_version"] if row else 0) or 0)


async def issue_access_token(username: str) -> str:
    """Access token carrying the user's current token_version."""
    return create_access_token(username, await current_token_version(username))


async def verify_token_current(token: str) -> Optional[str]:
    """verify_token + the user still exists and the token's version is the current one."""
    try:
        payload = _pyjwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
    if payload.get("type") not in (None, "access"):
        return None
    username = payload.get("sub")
    if not username:
        return None
    async with get_db() as db:
        row = await db.fetch_one("SELECT token_version FROM users WHERE username=?", (username,))
    if not row or (payload.get("ver") or 0) != (row["token_version"] or 0):
        return None
    return username


# ─── FastAPI dependency ───────────────────────────────────────────────────────
async def require_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> str:
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token requis"
        )
    try:
        username = await verify_token_current(credentials.credentials)
    except Exception:
        logger.exception("Access token check failed (database unavailable)")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Service indisponible"
        )
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token invalide"
        )
    return username


# ─── User management ──────────────────────────────────────────────────────────
async def user_exists() -> bool:
    async with get_db() as db:
        row = await db.fetch_one("SELECT COUNT(*) AS cnt FROM users")
        return (row["cnt"] or 0) > 0


async def create_user(username: str, password: str):
    async with get_db() as db:
        await db.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, hash_password(password), datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def get_user(username: str) -> Optional[dict]:
    async with get_db() as db:
        return await db.fetch_one(
            "SELECT * FROM users WHERE username=?", (username,)
        )


async def update_password(username: str, new_password: str):
    async with get_db() as db:
        await db.execute(
            "UPDATE users SET password_hash=? WHERE username=?",
            (hash_password(new_password), username),
        )
        await db.commit()


# ─── Rate limiting (SQLite-persisted, per-IP) ─────────────────────────────────
# Uses stdlib sqlite3 (synchronous) so rate_limit() stays sync.
# Falls back to in-memory when DB_PATH == ":memory:" (tests).
import sqlite3 as _sqlite3
import threading as _threading

_rate_buckets: dict = {}  # in-memory fallback (tests / MariaDB / DB error)
_rate_call_counter: int = 0
# rate_limit() runs in worker threads (asyncio.to_thread) — the dict
# read-modify-write below must be serialized or concurrent attempts from
# one IP would overwrite each other and under-count.
_rate_lock = _threading.Lock()

RATE_LIMIT_WINDOW = 300  # 5 minutes
RATE_LIMIT_MAX = 5  # 5 failed attempts


def _memory_rate_limit(key: str, now: float, cutoff: float) -> bool:
    global _rate_call_counter
    with _rate_lock:
        bucket = _rate_buckets.get(key, [])
        bucket = [t for t in bucket if t > cutoff]
        bucket.append(now)
        _rate_buckets[key] = bucket
        _rate_call_counter += 1
        if _rate_call_counter % 500 == 0:
            stale = [k for k, v in list(_rate_buckets.items()) if not v or v[-1] <= cutoff]
            for k in stale:
                del _rate_buckets[k]
        return len(bucket) > RATE_LIMIT_MAX


def _memory_rate_limit_attempt(key: str, now: float, cutoff: float) -> tuple:
    """Same atomic record-then-count as _memory_rate_limit, but also returns
    a token identifying this attempt so a caller that turns out to succeed
    can give it back via _memory_release_attempt() — see rate_limit_attempt()
    for why the record must happen atomically, before any slow verification.
    """
    blocked = _memory_rate_limit(key, now, cutoff)
    return (blocked, now)


def _memory_release_attempt(key: str, token: float) -> None:
    """Delete exactly the attempt recorded for `token` (its timestamp) —
    call on the SUCCESS path only, so a legitimate call never counts against
    the failure budget. No-op if it was already purged by the window.
    """
    with _rate_lock:
        bucket = _rate_buckets.get(key)
        if not bucket:
            return
        try:
            bucket.remove(token)
        except ValueError:
            pass  # already purged — nothing to release


def rate_limit(key: str) -> bool:
    """Returns True if the key has exceeded the limit. Records this attempt.

    Persists attempts to SQLite or MariaDB (shared across multi-worker
    deployments) so the window survives container restarts and is not
    multiplied by worker count. Falls back to in-memory for :memory:
    databases (test environment) or on a DB error.
    """
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW

    if DB_PATH == ":memory:":
        return _memory_rate_limit(key, now, cutoff)

    from .db.engine import DIALECT
    if DIALECT == "mariadb":
        try:
            from ._rate_limit_backend import mariadb_rate_limit
            return mariadb_rate_limit(key, now, cutoff, RATE_LIMIT_MAX)
        except Exception as e:
            logger.warning(f"rate_limit MariaDB error, falling back to in-memory: {e}")
            return _memory_rate_limit(key, now, cutoff)

    try:
        with _sqlite3.connect(DB_PATH, timeout=5) as conn:
            conn.execute("DELETE FROM rate_limit WHERE ts < ?", (cutoff,))
            conn.execute("INSERT INTO rate_limit (key, ts) VALUES (?, ?)", (key, now))
            cur = conn.execute(
                "SELECT COUNT(*) FROM rate_limit WHERE key = ? AND ts > ?",
                (key, cutoff),
            )
            count = cur.fetchone()[0]
            return count > RATE_LIMIT_MAX
    except Exception as e:
        logger.warning(f"rate_limit DB error, falling back to in-memory: {e}")
        return _memory_rate_limit(key, now, cutoff)


def rate_limit_attempt(key: str) -> tuple:
    """Atomically records this attempt and returns (blocked, token).

    This is the SAME atomic record-then-count as rate_limit() — deliberately
    NOT split into a check-then-later-record pair. A burst of N concurrent
    requests must not be able to bypass the limit: if the record only
    happened after a slow verification step (Argon2 hashing, ~170ms), every
    one of the N concurrent requests would read the pre-record count and
    pass the check before any of them recorded a failure — a TOCTOU that
    turns "5 attempts per window" into "N concurrent attempts per window".
    Recording immediately, before the slow step, closes that window: the
    (MAX+1)th concurrent request to reach the DB sees the earlier ones'
    rows and is blocked immediately, without even attempting verification.

    token identifies the row this call inserted, so a caller whose request
    turns out to be legitimate can hand it to release_attempt() to delete
    it — a successful call must not permanently count against the budget.
    """
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW

    if DB_PATH == ":memory:":
        return _memory_rate_limit_attempt(key, now, cutoff)

    from .db.engine import DIALECT
    if DIALECT == "mariadb":
        try:
            from ._rate_limit_backend import mariadb_rate_limit_attempt
            return mariadb_rate_limit_attempt(key, now, cutoff, RATE_LIMIT_MAX)
        except Exception as e:
            logger.warning(f"rate_limit_attempt MariaDB error, falling back to in-memory: {e}")
            return _memory_rate_limit_attempt(key, now, cutoff)

    try:
        with _sqlite3.connect(DB_PATH, timeout=5) as conn:
            conn.execute("DELETE FROM rate_limit WHERE ts < ?", (cutoff,))
            conn.execute("INSERT INTO rate_limit (key, ts) VALUES (?, ?)", (key, now))
            cur = conn.execute(
                "SELECT COUNT(*) FROM rate_limit WHERE key = ? AND ts > ?",
                (key, cutoff),
            )
            count = cur.fetchone()[0]
            return (count > RATE_LIMIT_MAX, now)
    except Exception as e:
        logger.warning(f"rate_limit_attempt DB error, falling back to in-memory: {e}")
        return _memory_rate_limit_attempt(key, now, cutoff)


def release_attempt(key: str, token) -> None:
    """Delete exactly the attempt recorded by rate_limit_attempt (token is
    its timestamp). Call this on the SUCCESS path only, so a legitimate
    call never counts against the failure budget. No-op if token is None
    (e.g. a caller that never called rate_limit_attempt).
    """
    if token is None:
        return

    if DB_PATH == ":memory:":
        _memory_release_attempt(key, token)
        return

    from .db.engine import DIALECT
    if DIALECT == "mariadb":
        try:
            from ._rate_limit_backend import mariadb_release_attempt
            mariadb_release_attempt(key, token)
            return
        except Exception as e:
            logger.warning(f"release_attempt MariaDB error, falling back to in-memory: {e}")
            _memory_release_attempt(key, token)
            return

    try:
        with _sqlite3.connect(DB_PATH, timeout=5) as conn:
            # Plain SQLite DELETE has no LIMIT clause (only with a non-default
            # compile flag) — the rowid subquery guarantees at most one row
            # is removed even if two attempts happened to share a timestamp.
            conn.execute(
                "DELETE FROM rate_limit WHERE rowid IN "
                "(SELECT rowid FROM rate_limit WHERE key = ? AND ts = ? LIMIT 1)",
                (key, token),
            )
    except Exception as e:
        logger.warning(f"release_attempt DB error, falling back to in-memory: {e}")
        _memory_release_attempt(key, token)


_warned_untrusted_forwarded_for = False
_warned_untrusted_headers = False
_background_tasks: set = set()

_TRUSTED_ENV = "HYGIE_TRUSTED_PROXIES"
_trusted_cache: tuple = (None, ())


def parse_trusted_proxies(raw: str) -> tuple[tuple, list[str]]:
    """Parse a comma-separated list of IPs/CIDRs. Returns (networks, invalid_entries)."""
    nets: list = []
    invalid: list[str] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            invalid.append(item)
    return tuple(nets), invalid


def trusted_networks() -> tuple:
    """Networks from HYGIE_TRUSTED_PROXIES; () when unset or when any entry is invalid.

    Cached by raw value so tests (and a changed env) are picked up.
    """
    global _trusted_cache
    raw = os.environ.get(_TRUSTED_ENV, "")
    if _trusted_cache[0] != raw:
        nets, invalid = parse_trusted_proxies(raw)
        _trusted_cache = (raw, () if invalid else nets)
    return _trusted_cache[1]


def proxy_trust_configured() -> bool:
    """True when real client IPs are visible (legacy flag or trusted list)."""
    return _TRUST_PROXY or bool(trusted_networks())


def _valid_ip(value: str) -> Optional[str]:
    """Return the normalized IP string, or None if `value` is not an IP."""
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def _normalize_ip(value: str):
    """ip_address with IPv4-mapped IPv6 collapsed to IPv4; None if invalid."""
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    mapped = getattr(ip, "ipv4_mapped", None)
    return mapped if mapped is not None else ip


def _is_trusted(ip, nets: tuple) -> bool:
    return any(ip in net for net in nets if net.version == ip.version)


def _trusted_proxy_ip(request: Request) -> Optional[str]:
    """Legacy mode (HYGIE_TRUST_PROXY=1, no list): client IP asserted by the proxy, or None.

    CF-Connecting-IP (set by the Cloudflare edge, which overwrites any
    client-supplied value) wins. Otherwise the RIGHTMOST valid
    X-Forwarded-For entry is used: it is the one appended by the closest
    trusted proxy, whereas the leftmost entries are client-controlled.
    """
    cf = request.headers.get("CF-Connecting-IP")
    if cf and (ip := _valid_ip(cf)):
        return ip
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        for part in reversed(fwd.split(",")):
            if ip := _valid_ip(part):
                return ip
    return None


def _warn_untrusted_headers_once(peer: str) -> None:
    """One WARN per process (logger + security journal) for proxy headers from an untrusted peer."""
    global _warned_untrusted_headers
    if _warned_untrusted_headers:
        return
    _warned_untrusted_headers = True
    msg = (
        f"En-têtes de proxy (X-Forwarded-For / CF-Connecting-IP) reçus d'un pair non fiable "
        f"({peer}) : ignorés. Si c'est votre reverse proxy, l'ajouter à {_TRUSTED_ENV}."
    )
    logger.warning(msg)
    try:
        from .db.logs import add_log
        task = asyncio.get_running_loop().create_task(add_log("WARN", msg, source="security"))
    except RuntimeError:
        return  # no running loop (sync caller): logger warning is enough
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _resolve_with_trusted_list(request: Request, nets: tuple) -> str:
    """Client IP when HYGIE_TRUSTED_PROXIES is set.

    Same algorithm as uvicorn's ProxyHeadersMiddleware (rightmost untrusted
    X-Forwarded-For entry), so the result is identical whether or not uvicorn
    already rewrote request.client.
    """
    raw_peer = request.client.host if request.client else None
    if not raw_peer:
        return "unknown"
    peer = _normalize_ip(raw_peer)
    xff = request.headers.get("X-Forwarded-For")
    cf = request.headers.get("CF-Connecting-IP")
    if peer is None or not _is_trusted(peer, nets):
        if xff or cf:
            seen = {_normalize_ip(p) for p in (xff or "").split(",")}
            seen.add(_normalize_ip(cf or ""))
            if peer not in seen:
                _warn_untrusted_headers_once(raw_peer)
        return raw_peer
    if xff:
        entries = [e for e in (_normalize_ip(p) for p in xff.split(",")) if e is not None]
        for ip in reversed(entries):
            if not _is_trusted(ip, nets):
                return str(ip)
        if entries:  # every hop is a trusted proxy: leftmost, as uvicorn does
            return str(entries[0])
    if cf and (ip := _valid_ip(cf)):
        return ip
    return raw_peer


def get_client_ip(request: Request) -> str:
    """Return the client IP for rate limiting.

    Proxy headers are only honoured from trusted peers: HYGIE_TRUSTED_PROXIES
    (IP/CIDR list, recommended) or the legacy HYGIE_TRUST_PROXY=1 flag.
    """
    global _warned_untrusted_forwarded_for
    nets = trusted_networks()
    if nets:
        return _resolve_with_trusted_list(request, nets)
    if _TRUST_PROXY:
        ip = _trusted_proxy_ip(request)
        if ip:
            return ip
    elif request.headers.get("X-Forwarded-For") and not _warned_untrusted_forwarded_for:
        # StartupValidator only catches this via HYGIE_ALLOWED_ORIGINS, which
        # stays unset on deployments behind a tunnel/proxy that doesn't need
        # CORS configured (e.g. Cloudflare Tunnel). A live X-Forwarded-For
        # header while untrusted is direct evidence rate limiting is keyed on
        # the proxy's IP — shared by everyone — instead of the real client.
        _warned_untrusted_forwarded_for = True
        logger.warning(
            "Requête reçue avec X-Forwarded-For mais HYGIE_TRUST_PROXY n'est pas activé — "
            "le rate limiting utilisera l'IP du proxy (partagée par tous) au lieu de l'IP réelle. "
            "Si l'app est derrière un reverse proxy/tunnel, définir HYGIE_TRUSTED_PROXIES "
            "(ex. 172.27.0.0/16) ou, à défaut, HYGIE_TRUST_PROXY=1."
        )
    return request.client.host if request.client else "unknown"
