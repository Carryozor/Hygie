"""Calendar — upcoming deletions grouped by date."""
from collections import defaultdict
from datetime import timedelta
from fastapi import APIRouter, Depends, Query

from ..auth import require_auth
from ..db.utils import parse_iso_dt, now_utc
from ..db.repositories import get_pending_before

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


@router.get("")
async def calendar(
    user: str = Depends(require_auth),
    days_ahead: int = Query(90, ge=1, le=365),
):
    """Return pending deletions grouped by day, up to days_ahead days from now."""
    cutoff_str = (now_utc() + timedelta(days=days_ahead)).isoformat()
    rows = await get_pending_before(cutoff_str)

    grouped: dict = defaultdict(list)
    for d in rows:
        d = dict(d)
        dt = parse_iso_dt(d.get("delete_at"))
        if not dt:
            continue
        key = dt.strftime("%Y-%m-%d")
        grouped[key].append(d)

    return {
        "events": {date: items for date, items in sorted(grouped.items())}
    }
