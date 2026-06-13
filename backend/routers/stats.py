"""Global statistics endpoint."""
from fastapi import APIRouter, Depends

from ..auth import require_auth
from ..db.engine import get_db
from ..db.repositories import get_status_counts
from ..db.utils import STATUS_DELETED

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/global")
async def global_stats(user: str = Depends(require_auth)):
    """Global lifetime statistics for the dashboard."""
    queue_counts = await get_status_counts()
    async with get_db() as db:
        row = await db.fetch_one("SELECT COALESCE(SUM(total_deleted),0) AS s FROM stats_history")
        from_history = row["s"] if row else 0

        in_queue = queue_counts.get(STATUS_DELETED, 0)
        total_deleted = max(from_history, in_queue)

        by_month_rows = await db.fetch_all(
            "SELECT month, SUM(total_deleted) AS deleted FROM stats_history "
            "GROUP BY month ORDER BY month DESC LIMIT 12"
        )
        by_month = [{"month": r["month"], "deleted": r["deleted"]} for r in by_month_rows]

        row = await db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM job_history WHERE job_type IN ('scan','scan_library')"
        )
        total_scans = row["cnt"] if row else 0

        row = await db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM job_history WHERE job_type='deletion_check'"
        )
        total_checks = row["cnt"] if row else 0

        row = await db.fetch_one("SELECT COUNT(*) AS cnt FROM ignored_media")
        total_ignored = row["cnt"] if row else 0

    return {
        "total_deleted": total_deleted,
        "total_ignored": total_ignored,
        "total_scans": total_scans,
        "total_deletion_checks": total_checks,
        "queue": queue_counts,
        "by_month": list(reversed(by_month)),
    }
