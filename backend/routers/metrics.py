"""Prometheus /metrics endpoint (no auth — scrape-friendly) + JSON /api/metrics."""
import aiosqlite
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from ..auth import require_auth
from ..db.utils import DB_PATH

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM media_queue WHERE status='pending'")
        pending = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM media_queue WHERE status='deleted'")
        deleted = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM media_queue WHERE status='error'")
        errors = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM ignored_media")
        ignored = (await cur.fetchone())[0]

        cur = await db.execute(
            "SELECT COUNT(*) FROM job_history WHERE job_type IN ('scan','scan_library')"
        )
        scans = (await cur.fetchone())[0]

        cur = await db.execute(
            "SELECT COUNT(*) FROM job_history WHERE job_type='deletion_check'"
        )
        checks = (await cur.fetchone())[0]

    lines = [
        "# HELP hygie_media_pending Media items pending deletion decision",
        "# TYPE hygie_media_pending gauge",
        f"hygie_media_pending {pending}",
        "# HELP hygie_media_deleted_total Media items deleted (lifetime)",
        "# TYPE hygie_media_deleted_total counter",
        f"hygie_media_deleted_total {deleted}",
        "# HELP hygie_media_ignored_total Media items in ignore list",
        "# TYPE hygie_media_ignored_total gauge",
        f"hygie_media_ignored_total {ignored}",
        "# HELP hygie_media_errors Media items with error status",
        "# TYPE hygie_media_errors gauge",
        f"hygie_media_errors {errors}",
        "# HELP hygie_scans_total Library scans performed (lifetime)",
        "# TYPE hygie_scans_total counter",
        f"hygie_scans_total {scans}",
        "# HELP hygie_deletion_checks_total Deletion condition checks performed (lifetime)",
        "# TYPE hygie_deletion_checks_total counter",
        f"hygie_deletion_checks_total {checks}",
        "",
    ]
    return "\n".join(lines)


@router.get("/api/metrics")
async def api_metrics(user: str = Depends(require_auth)):
    """JSON metrics endpoint with per-library breakdown for the current month."""
    from datetime import datetime, timezone

    current_month = datetime.now(timezone.utc).strftime("%Y-%m")

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT library_id, SUM(total_deleted), SUM(space_freed_bytes) "
            "FROM stats_history "
            "WHERE month = ? "
            "GROUP BY library_id",
            (current_month,),
        )
        rows = await cur.fetchall()

    by_library = [
        {
            "library_id": row[0],
            "deleted": row[1] or 0,
            "space_freed_bytes": row[2] or 0,
        }
        for row in rows
    ]

    return {"by_library": by_library}
