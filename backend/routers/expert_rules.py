"""Expert rules CRUD endpoints."""
import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..auth import require_auth
from ..db.utils import DB_PATH
from ..db.engine import SQLITE_PATH
from ..db.repositories import (
    save_expert_rule,
    get_expert_rules,
    get_expert_rule_by_id,
    delete_expert_rule,
)
from ..db.schema import _migrate_libraries_to_expert_rules
from ..rules.models import ExpertRule

router = APIRouter(prefix="/api/expert-rules", tags=["expert-rules"])


@router.get("")
async def list_expert_rules(user: str = Depends(require_auth)):
    rules = await get_expert_rules()
    return [r.model_dump() for r in rules]


@router.post("", status_code=201)
async def create_expert_rule(rule: ExpertRule, user: str = Depends(require_auth)):
    rule.id = None  # force INSERT
    rule_id = await save_expert_rule(rule)
    created = await get_expert_rule_by_id(rule_id)
    return created.model_dump()


@router.put("/{rule_id}")
async def update_expert_rule(
    rule_id: int, rule: ExpertRule, user: str = Depends(require_auth)
):
    existing = await get_expert_rule_by_id(rule_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Rule not found")
    rule.id = rule_id
    await save_expert_rule(rule)
    return (await get_expert_rule_by_id(rule_id)).model_dump()


@router.delete("/{rule_id}", status_code=204)
async def delete_expert_rule_endpoint(
    rule_id: int, user: str = Depends(require_auth)
):
    existing = await get_expert_rule_by_id(rule_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Rule not found")
    await delete_expert_rule(rule_id)
    return Response(status_code=204)


@router.post("/migrate-from-libraries")
async def migrate_from_libraries(user: str = Depends(require_auth)):
    """Re-run library → expert rules migration (idempotent: skips existing rules)."""
    async with aiosqlite.connect(SQLITE_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        n = await _migrate_libraries_to_expert_rules(db)
    return {"created": n}
