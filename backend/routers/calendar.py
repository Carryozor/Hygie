"""Calendar — upcoming deletions grouped by date."""
from collections import defaultdict
from datetime import datetime

import aiosqlite
from fastapi import APIRouter, Depends, Query

from ..auth import require_auth
from ..database import DB_PATH

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


@router.get("")
async def calendar(
    user: str = Depends(require_auth),
    days_ahead: int = Query(90, ge=1, le=365),
):
    """Return pending deletions grouped by day."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, title, media_type, library_name, delete_at, poster_url, "
            "seerr_username FROM media_queue WHERE status='pending' "
            "ORDER BY delete_at ASC"
        ) as cur:
            rows = await cur.fetchall()

    grouped: dict = defaultdict(list)
    for row in rows:
        d = dict(row)
        try:
            dt = datetime.fromisoformat(d["delete_at"].replace("Z", "+00:00"))
            key = dt.strftime("%Y-%m-%d")
            grouped[key].append(d)
        except Exception:
            continue

    # Return format: {"events": {"YYYY-MM-DD": [item, ...]}}
    # as expected by the frontend renderCalendar()
    return {
        "events": {date: items for date, items in sorted(grouped.items())}
    }
