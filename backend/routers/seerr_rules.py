"""Seerr user rules — Discord ID mappings + per-library grace overrides."""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import require_auth
from ..db.utils import DB_PATH
from ..db.engine import get_db
from ..arr_clients import seerr_get_users

router = APIRouter(prefix="/api/seerr-rules", tags=["seerr_rules"])
logger = logging.getLogger(__name__)


class RuleBody(BaseModel):
    seerr_user_id: int
    seerr_username: str
    library_id: str
    grace_days: int = 30
    enabled: bool = True
    discord_id: str = ""


class DiscordMappingBody(BaseModel):
    seerr_user_id: int
    seerr_username: str
    discord_id: str


@router.get("/users")
async def get_seerr_users(user: str = Depends(require_auth)):
    """List Seerr users with their Discord IDs (auto-detected from Seerr)."""
    users = await seerr_get_users()
    return users


@router.get("")
async def list_rules(user: str = Depends(require_auth)):
    async with get_db() as db:
        rows = await db.fetch_all(
            "SELECT * FROM seerr_user_rules ORDER BY seerr_username, library_id"
        )
    return rows


@router.post("")
async def create_rule(body: RuleBody, user: str = Depends(require_auth)):
    async with get_db() as db:
        new_id = await db.execute(
            """INSERT INTO seerr_user_rules
               (seerr_user_id, seerr_username, library_id, grace_days, enabled,
                discord_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                body.seerr_user_id,
                body.seerr_username,
                body.library_id,
                body.grace_days,
                int(body.enabled),
                body.discord_id,
            ),
        )
        await db.commit()
        return {"id": new_id}


@router.put("/{rule_id}")
async def update_rule(rule_id: int, body: RuleBody, user: str = Depends(require_auth)):
    async with get_db() as db:
        await db.execute(
            "UPDATE seerr_user_rules SET grace_days=?, enabled=?, discord_id=? WHERE id=?",
            (body.grace_days, int(body.enabled), body.discord_id, rule_id),
        )
        await db.commit()
    return {"status": "ok"}


@router.delete("/{rule_id}")
async def delete_rule(rule_id: int, user: str = Depends(require_auth)):
    async with get_db() as db:
        await db.execute("DELETE FROM seerr_user_rules WHERE id=?", (rule_id,))
        await db.commit()
    return {"status": "deleted"}


# ─── Discord ID global mappings ──────────────────────────────────────────────
@router.get("/discord-mappings")
async def get_discord_mappings(user: str = Depends(require_auth)):
    """List all known Seerr user → Discord ID mappings (one per user)."""
    async with get_db() as db:
        rows = await db.fetch_all(
            "SELECT DISTINCT seerr_user_id, seerr_username, discord_id "
            "FROM seerr_user_rules ORDER BY seerr_username"
        )
    return rows


@router.post("/discord-mappings")
async def save_discord_mapping(
    body: DiscordMappingBody, user: str = Depends(require_auth)
):
    """Set the Discord ID for a Seerr user (applies to all libraries)."""
    async with get_db() as db:
        # Check if rows already exist for this user
        count_row = await db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM seerr_user_rules WHERE CAST(seerr_user_id AS TEXT)=CAST(? AS TEXT)",
            (body.seerr_user_id,),
        )
        count = count_row["cnt"] if count_row else 0

        if count > 0:
            # Update discord_id on all existing rows for this user
            await db.execute(
                "UPDATE seerr_user_rules SET discord_id=? "
                "WHERE CAST(seerr_user_id AS TEXT)=CAST(? AS TEXT)",
                (body.discord_id, body.seerr_user_id),
            )
        else:
            # Insert a global mapping — omit created_at for legacy DB compat,
            # the migration will add it on next restart
            try:
                await db.execute(
                    """INSERT INTO seerr_user_rules
                       (seerr_user_id, seerr_username, library_id, grace_days,
                        enabled, discord_id, created_at)
                       VALUES (?, ?, '*', 30, 1, ?, ?)""",
                    (
                        body.seerr_user_id,
                        body.seerr_username,
                        body.discord_id,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            except Exception:
                # Fallback without created_at (legacy DB without that column)
                await db.execute(
                    """INSERT INTO seerr_user_rules
                       (seerr_user_id, seerr_username, library_id, grace_days,
                        enabled, discord_id)
                       VALUES (?, ?, '*', 30, 1, ?)""",
                    (body.seerr_user_id, body.seerr_username, body.discord_id),
                )
        await db.commit()
    return {"status": "saved"}
