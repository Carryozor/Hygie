"""Prometheus /metrics endpoint (no auth — scrape-friendly) + JSON /api/metrics."""
import hmac
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header
from fastapi.responses import PlainTextResponse, Response

from ..auth import require_auth
from ..db.engine import get_db
from ..db.repositories import get_status_counts
from ..db.settings_store import get_setting
from ..db.utils import STATUS_PENDING, STATUS_DELETED, STATUS_ERROR

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics(authorization: Optional[str] = Header(default=None)):
    token = (await get_setting("prometheus_bearer_token") or "").strip()
    if token:
        if not authorization or not authorization.startswith("Bearer "):
            return Response(status_code=401)
        if not hmac.compare_digest(authorization[7:].encode(), token.encode()):
            return Response(status_code=403)
    counts = await get_status_counts()
    pending = counts.get(STATUS_PENDING, 0)
    deleted = counts.get(STATUS_DELETED, 0)
    errors  = counts.get(STATUS_ERROR, 0)
    async with get_db() as db:
        row = await db.fetch_one("SELECT COUNT(*) AS cnt FROM ignored_media")
        ignored = row["cnt"] if row else 0

        row = await db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM job_history WHERE job_type IN ('scan','scan_library')"
        )
        scans = row["cnt"] if row else 0

        row = await db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM job_history WHERE job_type='deletion_check'"
        )
        checks = row["cnt"] if row else 0

    lines = [
        "# HELP hygie_media_pending Media items pending deletion decision",
        "# TYPE hygie_media_pending gauge",
        f"hygie_media_pending {pending}",
        "# HELP hygie_media_deleted_total Media items currently in deleted status",
        "# TYPE hygie_media_deleted_total gauge",
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
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")

    async with get_db() as db:
        rows = await db.fetch_all(
            "SELECT library_id, SUM(total_deleted) AS deleted, SUM(space_freed_bytes) AS freed "
            "FROM stats_history "
            "WHERE month = ? "
            "GROUP BY library_id",
            (current_month,),
        )

    by_library = [
        {
            "library_id": row["library_id"],
            "deleted": row["deleted"] or 0,
            "space_freed_bytes": row["freed"] or 0,
        }
        for row in rows
    ]

    return {"by_library": by_library}

from ..db.utils import DB_PATH  # noqa: F401 — monkeypatching target for tests
