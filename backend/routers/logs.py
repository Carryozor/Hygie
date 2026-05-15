"""Logs — list with filters."""
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, Query

from ..auth import require_auth
from ..database import DB_PATH

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
async def list_logs(
    user: str = Depends(require_auth),
    limit: int = Query(200, ge=1, le=5000),
    level: Optional[str] = None,
    source: Optional[str] = None,
    search: Optional[str] = None,
):
    where = []
    params = []
    if level:
        where.append("level = ?")
        params.append(level)
    if source:
        where.append("source = ?")
        params.append(source)
    if search:
        where.append("message LIKE ?")
        params.append(f"%{search}%")
    where_clause = f"WHERE {' AND '.join(where)}" if where else ""

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT * FROM logs {where_clause} ORDER BY ts DESC LIMIT ?",
            params + [limit],
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.delete("")
async def clear_logs(user: str = Depends(require_auth)):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM logs")
        await db.commit()
    return {"status": "cleared"}
