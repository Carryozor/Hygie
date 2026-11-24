"""Authentication event journal (4.6.0).

Every auth event is written to:
  1. stdout, as a stable one-line format (``HYGIE_AUTH v=1 ...``) parsed by
     fail2ban / CrowdSec (see SECURITY.md). Dedicated logger ``hygie_auth``,
     outside the ``hygie`` hierarchy so the ``log_level`` setting never mutes it.
  2. the Hygie journal (``logs`` table, source ``auth``), de-duplicated for
     noisy events.
  3. optionally Discord (successful logins only), in a background task.

Nothing here ever raises: auth flows must not fail because logging failed.
"""
import asyncio
import hashlib
import logging
import sys
import time
import unicodedata
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

from .db.engine import get_db
from .db.logs import add_log
from .db.settings_store import get_setting
from .logmsg import lm

logger = logging.getLogger("hygie.auth_events")

AUTH_LOGGER_NAME = "hygie_auth"
USERNAME_MAX_LEN = 64
DEDUP_WINDOW_SECONDS = 300
DEDUP_MAX_ENTRIES = 1024
_STRIPPED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
_FAILURE_EVENTS = frozenset({"login_failure", "mfa_failure", "locked", "refresh_rejected"})

# (event, ip, user) -> monotonic time of the last journal entry. Per process.
_dedup: dict[tuple[str, str, str], float] = {}
# Strong references to notification tasks (the loop only keeps weak ones).
_background_tasks: set[asyncio.Task] = set()


def _ensure_logger() -> logging.Logger:
    lg = logging.getLogger(AUTH_LOGGER_NAME)
    if not any(getattr(h, "_hygie_auth", False) for h in lg.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler._hygie_auth = True  # type: ignore[attr-defined]
        lg.addHandler(handler)
    lg.setLevel(logging.INFO)
    lg.propagate = False
    return lg


def _clean_username(raw: Optional[str]) -> str:
    """Strip control/format/line-separator chars (incl. bidi overrides), truncate."""
    kept = "".join(c for c in (raw or "") if unicodedata.category(c) not in _STRIPPED_CATEGORIES)
    return kept[:USERNAME_MAX_LEN]


def display_username(raw: Optional[str]) -> str:
    """Username safe for a space/`=`-delimited log line (percent-encoded). `-` if empty."""
    cleaned = _clean_username(raw)
    return quote(cleaned, safe="") if cleaned else "-"


def _token(value: str) -> str:
    out = "".join(c if (c.isascii() and (c.isalnum() or c == "_")) else "_" for c in (value or "").lower())
    return out or "unknown"


def format_auth_line(event: str, ip: str, username: Optional[str], method: str, reason: str) -> str:
    """One stdout line, format v1. Never contains a space/`=` inside a value."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    safe_ip = quote(ip or "-", safe=":.") or "-"
    return (
        f"{ts} HYGIE_AUTH v=1 event={_token(event)} ip={safe_ip} "
        f"method={_token(method)} reason={_token(reason)} user={display_username(username)}"
    )


def _should_journal(event: str, ip: str, user: str, reason: str) -> bool:
    """False when a noisy event was already journaled for this (event, ip, user) within 5 min."""
    if event != "locked" and reason != "challenge_invalid":
        return True
    now = time.monotonic()
    key = (event, ip, user)
    last = _dedup.get(key)
    if last is not None and now - last < DEDUP_WINDOW_SECONDS:
        return False
    if len(_dedup) >= DEDUP_MAX_ENTRIES:
        for k in [k for k, t in _dedup.items() if now - t >= DEDUP_WINDOW_SECONDS]:
            del _dedup[k]
        while len(_dedup) >= DEDUP_MAX_ENTRIES:
            del _dedup[next(iter(_dedup))]
    _dedup[key] = now
    return True


async def _notify_login(ip: str, user: str, method: str) -> None:
    try:
        if (await get_setting("discord_alert_login_success") or "true") == "false":
            return
        from . import discord_client
        await discord_client.send_alert(
            lm("auth.alert_title"),
            lm("auth.alert_body", user=user, ip=ip, method=method),
            level="info",
        )
    except Exception:
        logger.exception("login notification failed")


async def log_auth_event(
    event: str, *, ip: str, username: Optional[str], method: str,
    reason: str = "ok", notify: bool = False,
) -> None:
    """Record one auth event (stdout + journal + optional alert). Never raises."""
    try:
        _ensure_logger().info(format_auth_line(event, ip, username, method, reason))
    except Exception:
        logger.exception("auth stdout log failed")
    try:
        user = _clean_username(username) or "-"
        if _should_journal(event, ip, user, reason):
            level = "WARN" if event in _FAILURE_EVENTS else "INFO"
            msg = lm(f"auth.{event}", ip=ip, user=user, method=method, reason=reason)
            await add_log(level, msg, "auth")
    except Exception:
        logger.exception("auth journal log failed")
    try:
        if notify and event == "login_success" and method in ("password", "totp", "backup_code"):
            task = asyncio.create_task(_notify_login(ip, _clean_username(username) or "-", method))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
    except Exception:
        logger.exception("auth notification scheduling failed")


async def refresh_rejection_reason(raw_token: str) -> Optional[tuple[str, str]]:
    """Why was this refresh token rejected? ``(reason, username)`` or None if unknown.

    Called on the failure path only. A rotated token presented after its grace
    window is a replay (``reused``) even though verify already revoked it;
    otherwise a revoked row is ``revoked``.
    """
    try:
        if not raw_token:
            return None
        from .auth import _past_rotation_grace
        hashed = hashlib.sha256(raw_token.encode()).hexdigest()
        async with get_db() as db:
            row = await db.fetch_one(
                """SELECT u.username, rt.revoked, rt.rotated_at
                   FROM refresh_tokens rt JOIN users u ON u.id = rt.user_id
                   WHERE rt.token_hash = ?""",
                (hashed,),
            )
        if not row:
            return None
        if row["rotated_at"] and _past_rotation_grace(row["rotated_at"]):
            return "reused", row["username"]
        if row["revoked"]:
            return "revoked", row["username"]
        return None
    except Exception:
        logger.exception("refresh_rejection_reason failed")
        return None
