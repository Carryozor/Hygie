"""Auth routes — login, setup, password change."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import (
    create_access_token,
    create_refresh_token,
    create_user,
    get_client_ip,
    get_user,
    rate_limit,
    require_auth,
    revoke_all_refresh_tokens,
    revoke_refresh_token,
    update_password,
    user_exists,
    verify_password,
    verify_refresh_token,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)


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
    refresh_token: str

class LogoutRequest(BaseModel):
    refresh_token: str = ""


@router.get("/status")
async def auth_status():
    """Whether setup has been completed."""
    return {"setup_complete": await user_exists()}


@router.post("/setup")
async def setup(body: SetupRequest, request: Request):
    if await user_exists():
        raise HTTPException(409, "Un utilisateur existe déjà")
    ip = get_client_ip(request)
    if rate_limit(f"setup:{ip}"):
        raise HTTPException(429, "Trop de tentatives — réessayez dans 5 minutes")
    await create_user(body.username, body.password)
    access_token  = create_access_token(body.username)
    refresh_token = await create_refresh_token(body.username)
    return {
        "token":         access_token,
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "username":      body.username,
    }


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    ip   = get_client_ip(request)
    user = await get_user(body.username)
    if not user or not verify_password(body.password, user["password_hash"]):
        if rate_limit(f"login:{ip}"):
            raise HTTPException(429, "Trop de tentatives — réessayez dans 5 minutes")
        raise HTTPException(401, "Identifiants invalides")
    access_token  = create_access_token(user["username"])
    refresh_token = await create_refresh_token(user["username"])
    return {
        "token":         access_token,
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "username":      user["username"],
    }


@router.post("/refresh")
async def refresh(body: RefreshRequest):
    """Exchange a valid refresh token for a new access token."""
    if not body.refresh_token:
        raise HTTPException(401, "Refresh token requis")
    username = await verify_refresh_token(body.refresh_token)
    if not username:
        raise HTTPException(401, "Refresh token invalide ou expiré")
    access_token = create_access_token(username)
    return {"access_token": access_token, "token": access_token}


@router.post("/logout")
async def logout(body: LogoutRequest, username: str = Depends(require_auth)):
    """Revoke the provided refresh token."""
    if body.refresh_token:
        await revoke_refresh_token(body.refresh_token)
    return {"status": "logged_out"}


@router.post("/logout-all")
async def logout_all(username: str = Depends(require_auth)):
    """Revoke ALL refresh tokens for the current user."""
    await revoke_all_refresh_tokens(username)
    return {"status": "all_sessions_revoked"}


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest, username: str = Depends(require_auth)
):
    user = await get_user(username)
    if not user or not verify_password(body.current_password, user["password_hash"]):
        raise HTTPException(401, "Mot de passe actuel incorrect")
    await update_password(username, body.new_password)
    await revoke_all_refresh_tokens(username)
    access_token  = create_access_token(username)
    refresh_token = await create_refresh_token(username)
    return {
        "status":        "ok",
        "access_token":  access_token,
        "refresh_token": refresh_token,
    }


@router.get("/me")
async def me(username: str = Depends(require_auth)):
    return {"username": username}
