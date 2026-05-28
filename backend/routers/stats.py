"""Global statistics endpoint."""
import aiosqlite
from fastapi import APIRouter, Depends

from ..auth import require_auth
from ..db.utils import DB_PATH

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/global")
async def global_stats(user: str = Depends(require_auth)):
    """Global lifetime statistics for the dashboard."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COALESCE(SUM(total_deleted),0) FROM stats_history")
        from_history = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM media_queue WHERE status='deleted'")
        in_queue = (await cur.fetchone())[0]

        total_deleted = max(from_history, in_queue)

        cur = await db.execute(
            "SELECT month, SUM(total_deleted) FROM stats_history "
            "GROUP BY month ORDER BY month DESC LIMIT 12"
        )
        by_month = [{"month": r[0], "deleted": r[1]} for r in await cur.fetchall()]

        cur = await db.execute("SELECT status, COUNT(*) FROM media_queue GROUP BY status")
        queue_counts = {r[0]: r[1] for r in await cur.fetchall()}

        cur = await db.execute(
            "SELECT COUNT(*) FROM job_history WHERE job_type IN ('scan','scan_library')"
        )
        total_scans = (await cur.fetchone())[0]

        cur = await db.execute(
            "SELECT COUNT(*) FROM job_history WHERE job_type='deletion_check'"
        )
        total_checks = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM ignored_media")
        total_ignored = (await cur.fetchone())[0]

    return {
        "total_deleted": total_deleted,
        "total_ignored": total_ignored,
        "total_scans": total_scans,
        "total_deletion_checks": total_checks,
        "queue": queue_counts,
        "by_month": list(reversed(by_month)),
    }
