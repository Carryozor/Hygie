"""Ignored media — list, add, remove, with expiration."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import require_auth
from ..db.engine import get_db
from ..db.utils import escape_like

router = APIRouter(prefix="/api/ignored", tags=["ignored"])


class IgnoreBody(BaseModel):
    emby_id: str
    title: str
    media_type: Optional[str] = "Movie"
    library_id: Optional[str] = ""
    library_name: Optional[str] = ""
    poster_url: Optional[str] = ""
    reason: Optional[str] = ""
    expire_days: Optional[int] = None


@router.get("")
async def list_ignored(
    user: str = Depends(require_auth),
    search: Optional[str] = None,
    limit: int = Query(200, ge=1, le=10000),
):
    where = ""
    params = []
    if search:
        where = "WHERE title LIKE ? ESCAPE '!' OR reason LIKE ? ESCAPE '!'"
        s = f"%{escape_like(search)}%"
        params = [s, s]

    async with get_db() as db:
        rows = await db.fetch_all(
            f"SELECT * FROM ignored_media {where} ORDER BY ignored_at DESC LIMIT ?",
            params + [limit],
        )
    return rows


@router.post("")
async def add_ignored(
    body: IgnoreBody,
    background_tasks: BackgroundTasks,
    user: str = Depends(require_auth),
):
    expire_at = None
    if body.expire_days and body.expire_days > 0:
        expire_at = (
            datetime.now(timezone.utc) + timedelta(days=body.expire_days)
        ).isoformat()

    async with get_db() as db:
        await db.execute(
            """INSERT OR REPLACE INTO ignored_media
               (emby_id, title, media_type, library_id, library_name, poster_url,
                reason, ignored_at, expire_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                body.emby_id,
                body.title,
                body.media_type or "Movie",
                body.library_id or "",
                body.library_name or "",
                body.poster_url or "",
                body.reason or "",
                datetime.now(timezone.utc).isoformat(),
                expire_at,
            ),
        )
        # Also remove from queue if present
        await db.execute(
            "DELETE FROM media_queue WHERE emby_id=?", (body.emby_id,)
        )
        await db.commit()

    # Resync Emby collection in background
    from ..scheduler import sync_emby_collection
    background_tasks.add_task(sync_emby_collection)

    return {"status": "ignored"}


@router.delete("/{ignored_id}")
async def remove_ignored(ignored_id: int, user: str = Depends(require_auth)):
    async with get_db() as db:
        await db.execute("DELETE FROM ignored_media WHERE id=?", (ignored_id,))
        await db.commit()
    return {"status": "removed"}


@router.post("/{ignored_id}/requeue")
async def requeue_ignored(ignored_id: int, user: str = Depends(require_auth)):
    """
    Remove from ignored_media and immediately re-insert into media_queue
    so the item appears in the queue without waiting for the next scan.
    """
    from datetime import timedelta
    from ..db.utils import now_utc

    async with get_db() as db:
        item = await db.fetch_one(
            "SELECT * FROM ignored_media WHERE id=?", (ignored_id,)
        )
        if not item:
            raise HTTPException(404, "Entrée introuvable")

        # Check if already in queue (by emby_id)
        existing = await db.fetch_one(
            "SELECT id FROM media_queue WHERE emby_id=?", (item["emby_id"],)
        )

        if existing:
            # Just remove from ignored, already in queue
            await db.execute("DELETE FROM ignored_media WHERE id=?", (ignored_id,))
            await db.commit()
            return {"status": "already_in_queue"}

        # Find default grace days from any matching library
        grace_days = 7
        if item.get("library_id"):
            lib_row = await db.fetch_one(
                "SELECT grace_days FROM libraries WHERE id=?", (item["library_id"],)
            )
            if lib_row:
                grace_days = lib_row["grace_days"]

        delete_at = (now_utc() + timedelta(days=grace_days)).isoformat()
        detected_at = now_utc().isoformat()

        await db.execute(
            """INSERT INTO media_queue
               (emby_id, title, media_type, library_id, library_name, file_path,
                poster_url, tmdb_id, seerr_id, seerr_user_id, seerr_username,
                seerr_request_url, radarr_id, sonarr_id, sonarr_series_id, season_number,
                added_date, last_played, detected_at, delete_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (
                item["emby_id"], item["title"],
                item.get("media_type") or "Movie",
                item.get("library_id") or "", item.get("library_name") or "",
                item.get("file_path") or "", item.get("poster_url") or "",
                item.get("tmdb_id") or "", item.get("seerr_id"),
                item.get("seerr_user_id"), item.get("seerr_username") or "",
                item.get("seerr_request_url") or "",
                item.get("radarr_id"), item.get("sonarr_id"),
                None, None,
                item.get("added_date"), item.get("last_played"),
                detected_at, delete_at,
            ),
        )
        await db.execute("DELETE FROM ignored_media WHERE id=?", (ignored_id,))
        await db.commit()

    return {"status": "requeued", "delete_at": delete_at}
