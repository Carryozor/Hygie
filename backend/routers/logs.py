"""Logs — list with filters, mark-as-seen, acknowledge."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, field_validator

from ..auth import require_auth
from ..db.engine import get_db

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

    async with get_db() as db:
        rows = await db.fetch_all(
            f"SELECT * FROM logs {where_clause} ORDER BY ts DESC LIMIT ?",
            params + [limit],
        )
    return rows


class LogStatusUpdate(BaseModel):
    seen_status: Optional[str] = None  # 'seen' | 'acked' | None (clear)


@router.patch("/{log_id}")
async def update_log_status(log_id: int, body: LogStatusUpdate, user: str = Depends(require_auth)):
    """Mark a single log entry as seen or acknowledged."""
    status = body.seen_status if body.seen_status in ("seen", "acked") else None
    async with get_db() as db:
        await db.execute("UPDATE logs SET seen_status=? WHERE id=?", (status, log_id))
        await db.commit()
    return {"ok": True}


@router.get("/unseen-errors-count")
async def unseen_errors_count(user: str = Depends(require_auth)):
    """Return the number of ERROR logs not yet seen or acknowledged."""
    async with get_db() as db:
        row = await db.fetch_one(
            "SELECT COUNT(*) as n FROM logs WHERE level='ERROR' AND seen_status IS NULL"
        )
    return {"count": row["n"] if row else 0}


@router.post("/mark-seen-errors")
async def mark_seen_all_errors(user: str = Depends(require_auth)):
    """Mark all unseen ERROR logs as seen (green checkmark)."""
    async with get_db() as db:
        await db.execute(
            "UPDATE logs SET seen_status='seen' WHERE level='ERROR' AND seen_status IS NULL"
        )
        await db.commit()
    return {"ok": True}


@router.post("/ack-errors")
async def ack_all_errors(user: str = Depends(require_auth)):
    """Acknowledge all ERROR logs that haven't been seen yet (orange question mark)."""
    async with get_db() as db:
        await db.execute(
            "UPDATE logs SET seen_status='acked' WHERE level='ERROR' AND seen_status IS NULL"
        )
        await db.commit()
    return {"ok": True}


@router.delete("")
async def clear_logs(user: str = Depends(require_auth)):
    async with get_db() as db:
        await db.execute("DELETE FROM logs")
        await db.commit()
    return {"status": "cleared"}
