"""Auth routes — login, setup, password change."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import (
    create_token,
    create_user,
    get_client_ip,
    get_user,
    rate_limit,
    require_auth,
    update_password,
    user_exists,
    verify_password,
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


@router.get("/status")
async def auth_status():
    """Whether setup has been completed."""
    return {"setup_complete": await user_exists()}


@router.post("/setup")
async def setup(body: SetupRequest, request: Request):
    """Create the first (admin) user. Only allowed if no user exists."""
    if await user_exists():
        raise HTTPException(409, "Un utilisateur existe déjà")
    ip = get_client_ip(request)
    if rate_limit(f"setup:{ip}"):
        raise HTTPException(429, "Trop de tentatives — réessayez dans 5 minutes")
    await create_user(body.username, body.password)
    token = create_token(body.username)
    return {"token": token, "username": body.username}


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    """Login with rate limiting on failed attempts per IP."""
    ip = get_client_ip(request)
    user = await get_user(body.username)

    if not user or not verify_password(body.password, user["password_hash"]):
        # Record failure for rate limiting
        if rate_limit(f"login:{ip}"):
            raise HTTPException(429, "Trop de tentatives — réessayez dans 5 minutes")
        raise HTTPException(401, "Identifiants invalides")

    token = create_token(user["username"])
    return {"token": token, "username": user["username"]}


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest, username: str = Depends(require_auth)
):
    user = await get_user(username)
    if not user or not verify_password(body.current_password, user["password_hash"]):
        raise HTTPException(401, "Mot de passe actuel incorrect")
    await update_password(username, body.new_password)
    return {"status": "ok"}


@router.get("/me")
async def me(username: str = Depends(require_auth)):
    return {"username": username}
