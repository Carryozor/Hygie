"""Auth routes — login (with optional TOTP 2FA), setup, password change, 2FA management."""
import asyncio
import hashlib
import unicodedata
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import mfa
from ..auth_events import log_auth_event, refresh_rejection_reason
from ..db.engine import get_db

from ..auth import (
    issue_access_token,
    create_access_token,
    create_refresh_token,
    create_refresh_token_if_version,
    create_user,
    get_client_ip,
    get_user,
    rate_limit,
    rate_limit_attempt,
    release_attempt,
    require_auth,
    retire_refresh_token,
    revoke_all_refresh_tokens,
    revoke_refresh_token,
    update_password,
    user_exists,
    verify_password,
    verify_refresh_token,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)

# ── Refresh token cookie ───────────────────────────────────────────────────────
# The 30-day refresh token is delivered as an httpOnly cookie so XSS cannot
# exfiltrate it from localStorage. Scoped to /api/auth — it is only ever
# needed by the refresh/logout endpoints. The JSON body field is kept for
# one release for backward compatibility (old cached frontends, API users).
REFRESH_COOKIE = "hygie_refresh"
_REFRESH_COOKIE_PATH = "/api/auth"


def _set_refresh_cookie(response: Response, request: Request, raw_token: str) -> None:
    from ..auth import REFRESH_TOKEN_EXPIRE_DAYS
    response.set_cookie(
        REFRESH_COOKIE,
        raw_token,
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path=_REFRESH_COOKIE_PATH,
        httponly=True,
        samesite="strict",
        # Most self-hosted deployments run plain HTTP on the LAN — only mark
        # Secure when the request actually came over HTTPS.
        secure=(request.url.scheme == "https"),
    )


def _clear_refresh_cookie(response: Response, request: Request) -> None:
    """Expire the refresh cookie with the same attributes it was set with."""
    response.delete_cookie(
        REFRESH_COOKIE,
        path=_REFRESH_COOKIE_PATH,
        httponly=True,
        samesite="strict",
        secure=(request.url.scheme == "https"),
    )


def _fold_username(username: str) -> str:
    """Case/accent/width-insensitive form: MariaDB's default collation treats
    'admin', 'Admin', 'admín' and 'ａdmin' as the same account, so they must
    share one rate-limit budget instead of getting one each."""
    decomposed = unicodedata.normalize("NFKD", username.strip())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _rl_key(prefix: str, ip: str, username: str) -> str:
    """Rate-limit key scoped to IP + username (username hashed: bounded size)."""
    digest = hashlib.sha256(_fold_username(username).encode()).hexdigest()[:16]
    return f"{prefix}:{ip}:{digest}"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=200)


class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)


class MfaLoginRequest(BaseModel):
    mfa_token: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=32)


class MfaPasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)


class MfaEnableRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)


class MfaDisableRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=32)


class RefreshRequest(BaseModel):
    refresh_token: str = ""   # optional — the httpOnly cookie is preferred

class LogoutRequest(BaseModel):
    refresh_token: str = ""


@router.get("/status")
async def auth_status():
    """Whether setup has been completed."""
    return {"setup_complete": await user_exists()}


@router.post("/setup")
async def setup(body: SetupRequest, request: Request, response: Response):
    if await user_exists():
        raise HTTPException(409, "Un utilisateur existe déjà")
    ip = get_client_ip(request)
    # rate_limit fait du SQLite synchrone — exécuté hors de l'event loop
    if await asyncio.to_thread(rate_limit, f"setup:{ip}"):
        raise HTTPException(429, "Trop de tentatives — réessayez dans 5 minutes")
    await create_user(body.username, body.password)
    access_token  = await issue_access_token(body.username)
    refresh_token = await create_refresh_token(body.username)
    _set_refresh_cookie(response, request, refresh_token)
    return {
        "token":        access_token,
        "access_token": access_token,
        "username":     body.username,
    }


# Pre-computed Argon2id hash used when the username doesn't exist, so that
# verify_password() still runs and takes the same time — prevents username
# enumeration via response-time side-channel.
_DUMMY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4"
    "$fq695FCj/XKNaWy20+AgjQ"
    "$HxX7rtZRWI8Rbe2aP8xShI9CCWo50ZyKe2NHeedwsAg"
)


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response):
    ip = get_client_ip(request)
    # Atomic record-then-check BEFORE verification — a legitimate user is
    # never blocked (the attempt is released below on success), and a burst
    # of concurrent guesses can't bypass the limit by all reading a
    # pre-record count before any of them records (see rate_limit_attempt's
    # docstring). Never released == counted as a failure.
    rl_key = _rl_key("login", ip, body.username)
    blocked, rl_token = await asyncio.to_thread(rate_limit_attempt, rl_key)
    if blocked:
        await log_auth_event("locked", ip=ip, username=body.username, method="password", reason="rate_limit")
        raise HTTPException(429, "Trop de tentatives — réessayez dans 5 minutes")
    user = await get_user(body.username)
    # Toujours appeler verify_password pour éliminer le timing side-channel :
    # sans ceci, un username inexistant répond ~170ms plus vite (pas d'Argon2).
    # Argon2 est CPU-bound (~170ms) — exécuté hors de l'event loop.
    password_ok = await asyncio.to_thread(
        verify_password,
        body.password,
        user["password_hash"] if user else _DUMMY_HASH,
    )
    if not user or not password_ok:
        await log_auth_event(
            "login_failure", ip=ip, username=body.username, method="password", reason="invalid_credentials")
        raise HTTPException(401, "Identifiants invalides")
    await asyncio.to_thread(release_attempt, rl_key, rl_token)
    if user.get("totp_enabled"):
        # Password is right but no session is granted yet: no cookie, no access
        # token, only an opaque short-lived single-use challenge.
        challenge = await mfa.create_challenge(user["id"], user.get("token_version") or 0)
        return {"mfa_required": True, "mfa_token": challenge.token, "expires_in": challenge.expires_in}
    # `user` was read before Argon2: pin the session to THAT version so a 2FA
    # activation / password change landing meanwhile cannot leave a live session.
    ver = user.get("token_version") or 0
    refresh_token = await create_refresh_token_if_version(user["username"], ver)
    if refresh_token is None:
        await log_auth_event(
            "login_failure", ip=ip, username=body.username, method="password", reason="version")
        raise HTTPException(401, "Identifiants invalides")
    access_token = create_access_token(user["username"], ver)
    _set_refresh_cookie(response, request, refresh_token)
    await log_auth_event("login_success", ip=ip, username=user["username"], method="password", notify=True)
    return {
        "token":        access_token,
        "access_token": access_token,
        "username":     user["username"],
    }


@router.post("/refresh")
async def refresh(body: RefreshRequest, request: Request, response: Response):
    """Exchange a valid refresh token for a new access token.

    The token is read from the httpOnly cookie first, then from the JSON
    body (backward compatibility with pre-cookie clients).

    Rotation: each successful refresh issues a NEW refresh token cookie and
    retires the old token after a short grace window — a stolen cookie stops
    working at the next legitimate refresh instead of lasting 30 days.
    """
    raw = request.cookies.get(REFRESH_COOKIE, "") or body.refresh_token
    if not raw:
        # No credential presented (e.g. the login page probing on load):
        # must not consume the per-IP budget shared with real refreshes.
        raise HTTPException(401, "Refresh token requis")
    ip = get_client_ip(request)
    blocked, rl_token = await asyncio.to_thread(rate_limit_attempt, f"refresh:{ip}")
    if blocked:
        await log_auth_event("locked", ip=ip, username=None, method="refresh", reason="rate_limit")
        raise HTTPException(429, "Trop de tentatives — réessayez dans 5 minutes")
    verified = await verify_refresh_token(raw)
    if not verified:
        why = await refresh_rejection_reason(raw)
        await log_auth_event(
            "refresh_rejected", ip=ip, username=why[1] if why else None,
            method="refresh", reason=why[0] if why else "revoked")
        raise HTTPException(401, "Refresh token invalide ou expiré")
    username, version = verified
    await asyncio.to_thread(release_attempt, f"refresh:{ip}", rl_token)

    # The new refresh token is only minted if the version read with the old one
    # is still current (row-locked check): a logout-all landing in between makes
    # this return None instead of leaving a surviving session.
    new_refresh = await create_refresh_token_if_version(username, version)
    if new_refresh is None:
        await log_auth_event("refresh_rejected", ip=ip, username=username, method="refresh", reason="version")
        raise HTTPException(401, "Session révoquée")
    access_token = create_access_token(username, version)
    # Grace window: concurrent tabs may still hold the old cookie value.
    await retire_refresh_token(raw, grace_seconds=60)
    _set_refresh_cookie(response, request, new_refresh)

    return {"access_token": access_token, "token": access_token}


@router.post("/logout")
async def logout(body: LogoutRequest, request: Request, response: Response):
    """Revoke the presented refresh token (if any) and ALWAYS clear the cookie.

    Authenticated by the refresh cookie alone, not by the access token: the
    access token is short-lived, and a logout after its expiry must still
    end the session. Idempotent — returns 200 in every case.
    """
    raw = request.cookies.get(REFRESH_COOKIE, "") or body.refresh_token
    if raw:
        await revoke_refresh_token(raw)
    _clear_refresh_cookie(response, request)
    return {"status": "logged_out"}


@router.post("/logout-all")
async def logout_all(username: str = Depends(require_auth)):
    """Revoke ALL refresh tokens for the current user."""
    await revoke_all_refresh_tokens(username)
    return {"status": "all_sessions_revoked"}


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    response: Response,
    username: str = Depends(require_auth),
):
    rl_key = _rl_key("chpw", get_client_ip(request), username)
    blocked, rl_token = await asyncio.to_thread(rate_limit_attempt, rl_key)
    if blocked:
        raise HTTPException(429, "Trop de tentatives — réessayez dans 5 minutes")
    user = await get_user(username)
    if not user or not await asyncio.to_thread(
        verify_password, body.current_password, user["password_hash"]
    ):
        raise HTTPException(401, "Mot de passe actuel incorrect")
    await asyncio.to_thread(release_attempt, rl_key, rl_token)
    await update_password(username, body.new_password)
    await revoke_all_refresh_tokens(username)
    access_token  = await issue_access_token(username)
    refresh_token = await create_refresh_token(username)
    _set_refresh_cookie(response, request, refresh_token)
    return {
        "status":       "ok",
        "access_token": access_token,
    }


@router.get("/me")
async def me(username: str = Depends(require_auth)):
    return {"username": username}


# ── Two-factor authentication ──────────────────────────────────────────────────
_NO_STORE = {"Cache-Control": "no-store"}
_ERR_CHALLENGE = (401, "mfa_challenge_invalid", "Session de connexion expirée — reconnectez-vous")
_ERR_LOGIN_CODE = (401, "mfa_code_invalid", "Code invalide")
_ERR_LOCKED = (429, "mfa_locked", "Trop de tentatives — réessayez plus tard")
_ERR_CODE = (400, "mfa_code_invalid", "Code invalide")
_ERR_PASSWORD = (400, "invalid_password", "Mot de passe incorrect")
_ERR_RATE = (429, "rate_limited", "Trop de tentatives — réessayez dans 5 minutes")
_ERR_ALREADY = (409, "mfa_already_enabled", "La double authentification est déjà activée")
_ERR_NOT_ENABLED = (409, "mfa_not_enabled", "La double authentification n'est pas activée")
_ERR_UNREADABLE = (503, "mfa_secret_unreadable", "Secret 2FA illisible — utilisez un code de secours")


def _err(spec: tuple) -> JSONResponse:
    """Error body of the 2FA endpoints: French message + stable machine code."""
    status_code, code, detail = spec
    return JSONResponse({"detail": detail, "code": code}, status_code=status_code)


async def _username_of(user_id: int):
    async with get_db() as db:
        row = await db.fetch_one("SELECT username FROM users WHERE id=?", (user_id,))
    return row["username"] if row else None


async def _check_password(request: Request, user: dict, password: str):
    """Rate-limited password check (setup / disable). None when right, else the error response."""
    ip = get_client_ip(request)
    rl_key = _rl_key("mfa", ip, user["username"])
    blocked, rl_token = await asyncio.to_thread(rate_limit_attempt, rl_key)
    if blocked:
        await log_auth_event("locked", ip=ip, username=user["username"], method="password", reason="rate_limit")
        return _err(_ERR_RATE)
    if not await asyncio.to_thread(verify_password, password, user["password_hash"]):
        await log_auth_event(
            "mfa_failure", ip=ip, username=user["username"], method="password", reason="invalid_credentials")
        return _err(_ERR_PASSWORD)
    await asyncio.to_thread(release_attempt, rl_key, rl_token)
    return None


async def _account_code_budget(ip: str, user_id: int, username: str):
    """Per-account 5 / 5 min budget for code guesses, whatever the IP.

    Returns (error_response | None, rl_key, rl_token); release the attempt on success."""
    key = f"mfa:{user_id}"
    blocked, token = await asyncio.to_thread(rate_limit_attempt, key)
    if blocked:
        await log_auth_event("locked", ip=ip, username=username, method="mfa", reason="rate_limit")
        return _err(_ERR_LOCKED), key, token
    return None, key, token


async def _rotated_session_response(request: Request, username: str, payload: dict) -> JSONResponse:
    """Kill every session of the user, then answer with a fresh access token + cookie."""
    await revoke_all_refresh_tokens(username)
    result = JSONResponse({**payload, "access_token": await issue_access_token(username)}, headers=_NO_STORE)
    _set_refresh_cookie(result, request, await create_refresh_token(username))
    return result


@router.post("/login/mfa")
async def login_mfa(body: MfaLoginRequest, request: Request, response: Response):
    ip = get_client_ip(request)

    async def reject(spec, reason, username=None, method="mfa"):
        await log_auth_event("mfa_failure", ip=ip, username=username, method=method, reason=reason)
        return _err(spec)

    # 1. atomic claim of one attempt (TTL, single use, 3 tries, token_version)
    try:
        chal = await mfa.claim_challenge(body.mfa_token)
    except mfa.ChallengeInvalid:
        return await reject(_ERR_CHALLENGE, "challenge_invalid")
    username = await _username_of(chal.user_id)
    if username is None:
        return await reject(_ERR_CHALLENGE, "challenge_invalid")
    # 2. per-account limiter, then the 24 h cap
    limited, rl_key, rl_token = await _account_code_budget(ip, chal.user_id, username)
    if limited is not None:
        return limited
    try:
        await mfa.assert_under_daily_cap(chal.user_id)
    except mfa.MfaDailyCapReached:
        await log_auth_event("locked", ip=ip, username=username, method="mfa", reason="daily_cap")
        return _err(_ERR_LOCKED)
    # 3. the code itself (never logged)
    try:
        check = await mfa.check_code(chal.user_id, body.code)
    except mfa.MfaNotEnabled:
        return await reject(_ERR_CHALLENGE, "challenge_invalid", username)
    except mfa.MfaSecretUnreadable:
        logger.error("2FA secret of user id %s cannot be decrypted", chal.user_id)
        return await reject(_ERR_UNREADABLE, "invalid_code", username, "totp")
    if not check.ok:
        return await reject(_ERR_LOGIN_CODE, check.reason, username, check.method)
    await asyncio.to_thread(release_attempt, rl_key, rl_token)
    # 4. consume, then mint the session with the 4.5.1 race guarantees
    if not await mfa.consume_challenge(chal.id):
        return await reject(_ERR_CHALLENGE, "challenge_invalid", username)
    refresh_token = await create_refresh_token_if_version(username, chal.token_version)
    if refresh_token is None:
        return await reject(_ERR_CHALLENGE, "challenge_invalid", username)
    access_token = create_access_token(username, chal.token_version)
    _set_refresh_cookie(response, request, refresh_token)
    await log_auth_event("login_success", ip=ip, username=username, method=check.method, notify=True)
    return {"token": access_token, "access_token": access_token, "username": username}


@router.get("/mfa")
async def mfa_state(username: str = Depends(require_auth)):
    user = await get_user(username)
    st = await mfa.get_state(user["id"])
    return {
        "enabled": st.enabled,
        "enabled_at": st.enabled_at,
        "pending": st.pending,
        "backup_codes_remaining": st.backup_codes_remaining,
        "encryption_available": st.encryption_available,
    }


@router.post("/mfa/setup")
async def mfa_setup(body: MfaPasswordRequest, request: Request, username: str = Depends(require_auth)):
    user = await get_user(username)
    if (err := await _check_password(request, user, body.current_password)) is not None:
        return err
    try:
        info = await mfa.begin_setup(user["id"], username)
    except mfa.MfaAlreadyEnabled:
        return _err(_ERR_ALREADY)
    except mfa.EncryptionUnavailable:
        return _err((409, "encryption_unavailable",
                     "Chiffrement indisponible : définissez HYGIE_ENCRYPTION_KEY pour activer la double authentification"))
    return JSONResponse(
        {"secret": info.secret, "otpauth_uri": info.otpauth_uri, "qr_svg_data_uri": info.qr_svg_data_uri},
        headers=_NO_STORE,
    )


@router.post("/mfa/enable")
async def mfa_enable(body: MfaEnableRequest, request: Request, username: str = Depends(require_auth)):
    user = await get_user(username)
    ip = get_client_ip(request)
    limited, rl_key, rl_token = await _account_code_budget(ip, user["id"], username)
    if limited is not None:
        return limited
    try:
        backup_codes = await mfa.enable(user["id"], body.code)
    except mfa.MfaNoPendingSetup:
        return _err((400, "mfa_no_pending_setup", "Aucune configuration en attente — recommencez l'activation"))
    except mfa.MfaAlreadyEnabled:
        return _err(_ERR_ALREADY)
    except mfa.MfaCodeInvalid:
        await log_auth_event("mfa_failure", ip=ip, username=username, method="totp", reason="invalid_code")
        return _err(_ERR_CODE)
    except mfa.MfaSecretUnreadable:
        return _err(_ERR_UNREADABLE)
    await asyncio.to_thread(release_attempt, rl_key, rl_token)
    result = await _rotated_session_response(request, username, {"enabled": True, "backup_codes": backup_codes})
    await log_auth_event("mfa_enabled", ip=ip, username=username, method="totp")
    return result


@router.post("/mfa/disable")
async def mfa_disable(body: MfaDisableRequest, request: Request, username: str = Depends(require_auth)):
    user = await get_user(username)
    ip = get_client_ip(request)
    if not user.get("totp_enabled"):
        return _err(_ERR_NOT_ENABLED)
    if (err := await _check_password(request, user, body.current_password)) is not None:
        return err
    limited, rl_key, rl_token = await _account_code_budget(ip, user["id"], username)
    if limited is not None:
        return limited
    try:
        check = await mfa.check_code(user["id"], body.code)
    except mfa.MfaNotEnabled:
        return _err(_ERR_NOT_ENABLED)
    except mfa.MfaSecretUnreadable:
        return _err(_ERR_UNREADABLE)
    if not check.ok:
        await log_auth_event("mfa_failure", ip=ip, username=username, method=check.method, reason=check.reason)
        return _err(_ERR_CODE)
    await asyncio.to_thread(release_attempt, rl_key, rl_token)
    try:
        await mfa.disable(user["id"])
    except mfa.MfaNotEnabled:
        return _err(_ERR_NOT_ENABLED)
    result = await _rotated_session_response(request, username, {"enabled": False})
    await log_auth_event("mfa_disabled", ip=ip, username=username, method=check.method)
    return result
