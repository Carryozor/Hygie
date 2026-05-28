"""Media queue — listing, search, sort, bulk actions, manual deletion."""
import logging
from datetime import datetime, timezone
from typing import List, Optional

import aiosqlite
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import require_auth
from ..database import DB_PATH, STATUS_PENDING, STATUS_DELETED, STATUS_ERROR, add_log, get_setting
from ..deletion import _delete_media
from ..conditions import _get_poster_url
from ..arr_clients import seerr_find_request_by_tmdb

router = APIRouter(prefix="/api/media", tags=["media"])
logger = logging.getLogger(__name__)


# Explicit safe mapping — column names validated at definition time
_SORT_MAP: dict = {
    "title":          "title",
    "library_name":   "library_name",
    "media_type":     "media_type",
    "delete_at":      "delete_at",
    "detected_at":    "detected_at",
    "added_date":     "added_date",
    "last_played":    "last_played",
    "status":         "status",
    "seerr_username": "seerr_username",
}
_SORT_FIELDS = set(_SORT_MAP.keys())


class BulkAction(BaseModel):
    ids: List[int] = Field(min_length=1)
    action: str  # "ignore" | "delete"
    reason: Optional[str] = None
    expire_days: Optional[int] = None  # for ignore


@router.get("/stats")
async def stats(user: str = Depends(require_auth)):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT status, COUNT(*) FROM media_queue GROUP BY status"
        ) as cur:
            rows = await cur.fetchall()
    counts = {status: c for status, c in rows}
    return {
        "pending": counts.get(STATUS_PENDING, 0),
        "deleted": counts.get(STATUS_DELETED, 0),
        "error": counts.get(STATUS_ERROR, 0),
        "total": sum(counts.values()),
    }


@router.get("")
async def list_queue(
    user: str = Depends(require_auth),
    limit: int = Query(50, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    status: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "delete_at",
    dir: str = "asc",
):
    sort_col = _SORT_MAP.get(sort, "delete_at")  # safe mapping — never raw interpolation
    dir = "DESC" if dir.lower() == "desc" else "ASC"

    where = []
    params = []
    if status:
        where.append("status = ?")
        params.append(status)
    if search:
        where.append("(title LIKE ? OR library_name LIKE ? OR seerr_username LIKE ?)")
        s = f"%{search}%"
        params.extend([s, s, s])

    where_clause = f"WHERE {' AND '.join(where)}" if where else ""

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT COUNT(*) FROM media_queue {where_clause}", params
        ) as cur:
            total = (await cur.fetchone())[0]

        async with db.execute(
            f"SELECT * FROM media_queue {where_clause} "
            f"ORDER BY {sort_col} {dir} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ) as cur:
            items = [dict(r) for r in await cur.fetchall()]

    return {"items": items, "total": total}


@router.post("/{media_id}/delete-now")
async def delete_now(
    media_id: int, user: str = Depends(require_auth)
):
    """Manually trigger deletion of a single item."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM media_queue WHERE id=?", (media_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                raise HTTPException(404, "Média introuvable")
            row = dict(row)

    dry_run = (await get_setting("dry_run") or "false").lower() == "true"
    ok = await _delete_media(row, dry_run)

    if ok:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE media_queue SET status='deleted', notified_now=1 WHERE id=?",
                (media_id,),
            )
            await db.commit()
        return {"status": "deleted"}
    raise HTTPException(500, "Suppression échouée")


@router.post("/bulk")
async def bulk(body: BulkAction, user: str = Depends(require_auth)):
    """Bulk action on selected items."""
    placeholders = ",".join("?" * len(body.ids))

    if body.action == "ignore":
        ignored_at = datetime.now(timezone.utc).isoformat()
        expire_at = None
        if body.expire_days and body.expire_days > 0:
            from datetime import timedelta
            expire_at = (
                datetime.now(timezone.utc) + timedelta(days=body.expire_days)
            ).isoformat()

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT id, emby_id, title, media_type, library_id, library_name, poster_url "
                f"FROM media_queue WHERE id IN ({placeholders})",
                body.ids,
            ) as cur:
                rows = await cur.fetchall()
            for row in rows:
                await db.execute(
                    """INSERT OR REPLACE INTO ignored_media
                       (emby_id, title, media_type, library_id, library_name, poster_url,
                        reason, ignored_at, expire_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (row["emby_id"], row["title"], row.get("media_type") or "Movie",
                     row.get("library_id") or "", row.get("library_name") or "",
                     row.get("poster_url") or "",
                     body.reason or "", ignored_at, expire_at),
                )
                await db.execute("DELETE FROM media_queue WHERE id=?", (row["id"],))
            await db.commit()
        return {"affected": len(rows)}

    if body.action == "delete":
        dry_run = (await get_setting("dry_run") or "false").lower() == "true"
        success = 0
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT * FROM media_queue WHERE id IN ({placeholders})",
                body.ids,
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
        for row in rows:
            if await _delete_media(row, dry_run):
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE media_queue SET status='deleted', notified_now=1 WHERE id=?",
                        (row["id"],),
                    )
                    await db.commit()
                success += 1
        return {"affected": success}

    raise HTTPException(400, "Action invalide")


@router.post("/{media_id}/ignore")
async def ignore_one(
    media_id: int,
    reason: Optional[str] = None,
    expire_days: Optional[int] = None,
    user: str = Depends(require_auth),
):
    from datetime import timedelta
    expire_at = None
    if expire_days and expire_days > 0:
        expire_at = (datetime.now(timezone.utc) + timedelta(days=expire_days)).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM media_queue WHERE id=?", (media_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                raise HTTPException(404, "Média introuvable")
        row = dict(row)
        await db.execute(
            "INSERT OR REPLACE INTO ignored_media "
            "(emby_id, title, media_type, library_id, library_name, file_path, "
            "poster_url, tmdb_id, seerr_id, seerr_user_id, seerr_username, "
            "seerr_request_url, radarr_id, sonarr_id, added_date, last_played, "
            "reason, ignored_at, expire_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["emby_id"], row["title"],
                row.get("media_type") or "Movie",
                row.get("library_id") or "", row.get("library_name") or "",
                row.get("file_path") or "", row.get("poster_url") or "",
                row.get("tmdb_id") or "", row.get("seerr_id"),
                row.get("seerr_user_id"), row.get("seerr_username") or "",
                row.get("seerr_request_url") or "",
                row.get("radarr_id"), row.get("sonarr_id"),
                row.get("added_date"), row.get("last_played"),
                reason or "", datetime.now(timezone.utc).isoformat(), expire_at,
            ),
        )
        await db.execute("DELETE FROM media_queue WHERE id=?", (media_id,))
        await db.commit()
    return {"status": "ignored"}


@router.delete("/purge/deleted")
async def purge_deleted(user: str = Depends(require_auth)):
    """Remove all entries with status='deleted' from the queue."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM media_queue WHERE status='deleted'"
        ) as cur:
            count = (await cur.fetchone())[0]
        await db.execute("DELETE FROM media_queue WHERE status='deleted'")
        await db.commit()
    await add_log("INFO", f"Purgé : {count} entrée(s) supprimée(s)", "system")
    return {"purged": count}


@router.post("/enrich-seerr")
async def enrich_seerr(
    background_tasks: BackgroundTasks, user: str = Depends(require_auth)
):
    """Re-fetch Seerr requester info AND regenerate poster URLs for all items."""
    async def _enrich():
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id, emby_id, tmdb_id, media_type, radarr_id, sonarr_id, "
                "seerr_username FROM media_queue"
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]

        enriched = 0
        for row in rows:
            updates = {}

            # Refresh poster URL
            new_url = await _get_poster_url(
                row["emby_id"],
                tmdb_id=row.get("tmdb_id") or "",
                media_type=row.get("media_type") or "Movie",
                radarr_id=row.get("radarr_id"),
                sonarr_id=row.get("sonarr_id"),
            )
            if new_url:
                updates["poster_url"] = new_url

            # Refresh Seerr requester
            if row.get("tmdb_id") and not row.get("seerr_username"):
                seerr_data = await seerr_find_request_by_tmdb(row["tmdb_id"])
                if seerr_data:
                    updates["seerr_id"] = seerr_data["seerr_id"]
                    updates["seerr_user_id"] = seerr_data["user_id"]
                    updates["seerr_username"] = seerr_data["username"]
                    ext = await get_setting("seerr_external_url")
                    if ext:
                        path = "movie" if row.get("media_type") == "Movie" else "tv"
                        updates["seerr_request_url"] = (
                            f"{ext.rstrip('/')}/{path}/{row['tmdb_id']}"
                        )

            if updates:
                set_clause = ", ".join(f"{k}=?" for k in updates.keys())
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        f"UPDATE media_queue SET {set_clause} WHERE id=?",
                        list(updates.values()) + [row["id"]],
                    )
                    await db.commit()
                enriched += 1

        await add_log(
            "INFO", f"Enrichissement terminé : {enriched}/{len(rows)} entrées", "system"
        )

    background_tasks.add_task(_enrich)
    return {"status": "started"}


@router.post("/regenerate-posters")
async def regenerate_posters(
    background_tasks: BackgroundTasks, user: str = Depends(require_auth)
):
    """Regenerate poster URLs for all queue items (using Radarr/Sonarr TMDB)."""
    async def _regen():
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id, emby_id, tmdb_id, media_type, radarr_id, sonarr_id FROM media_queue"
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]

        updated = 0
        for row in rows:
            new_url = await _get_poster_url(
                row["emby_id"],
                tmdb_id=row.get("tmdb_id") or "",
                media_type=row.get("media_type") or "Movie",
                radarr_id=row.get("radarr_id"),
                sonarr_id=row.get("sonarr_id"),
            )
            if new_url:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE media_queue SET poster_url=? WHERE id=?",
                        (new_url, row["id"]),
                    )
                    await db.commit()
                updated += 1
        await add_log(
            "INFO", f"Affiches régénérées : {updated}/{len(rows)}", "system"
        )

    background_tasks.add_task(_regen)
    return {"status": "started"}


@router.delete("/{media_id}/remove")
@router.delete("/{media_id}")
async def remove_from_queue(media_id: int, user: str = Depends(require_auth)):
    """Remove an entry from the queue without deleting the media itself."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM media_queue WHERE id=?", (media_id,))
        await db.commit()
    return {"status": "removed"}
