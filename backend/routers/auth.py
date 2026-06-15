"""Auth routes — login, setup, password change."""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..auth import (
    create_access_token,
    create_refresh_token,
    create_user,
    get_client_ip,
    get_user,
    rate_limit,
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


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=_REFRESH_COOKIE_PATH)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=200)


class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)


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
    access_token  = create_access_token(body.username)
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
    # Rate limit AVANT la vérification — empêche le bypass par alternance
    # succès/échec et protège toutes les tentatives, valides ou non.
    if await asyncio.to_thread(rate_limit, f"login:{ip}"):
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
        raise HTTPException(401, "Identifiants invalides")
    access_token  = create_access_token(user["username"])
    refresh_token = await create_refresh_token(user["username"])
    _set_refresh_cookie(response, request, refresh_token)
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
    ip = get_client_ip(request)
    if await asyncio.to_thread(rate_limit, f"refresh:{ip}"):
        raise HTTPException(429, "Trop de tentatives — réessayez dans 5 minutes")
    raw = request.cookies.get(REFRESH_COOKIE, "") or body.refresh_token
    if not raw:
        raise HTTPException(401, "Refresh token requis")
    username = await verify_refresh_token(raw)
    if not username:
        raise HTTPException(401, "Refresh token invalide ou expiré")
    access_token = create_access_token(username)

    new_refresh = await create_refresh_token(username)
    # Grace window: concurrent tabs may still hold the old cookie value.
    await retire_refresh_token(raw, grace_seconds=60)
    _set_refresh_cookie(response, request, new_refresh)

    return {"access_token": access_token, "token": access_token}


@router.post("/logout")
async def logout(
    body: LogoutRequest,
    request: Request,
    response: Response,
    username: str = Depends(require_auth),
):
    """Revoke the refresh token and clear the cookie.

    Cookie-first, like /refresh — the body field only serves legacy clients.
    """
    raw = request.cookies.get(REFRESH_COOKIE, "") or body.refresh_token
    if raw:
        await revoke_refresh_token(raw)
    _clear_refresh_cookie(response)
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
    user = await get_user(username)
    if not user or not await asyncio.to_thread(
        verify_password, body.current_password, user["password_hash"]
    ):
        raise HTTPException(401, "Mot de passe actuel incorrect")
    await update_password(username, body.new_password)
    await revoke_all_refresh_tokens(username)
    access_token  = create_access_token(username)
    refresh_token = await create_refresh_token(username)
    _set_refresh_cookie(response, request, refresh_token)
    return {
        "status":       "ok",
        "access_token": access_token,
    }


@router.get("/me")
async def me(username: str = Depends(require_auth)):
    return {"username": username}
