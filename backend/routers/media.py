"""Media queue — listing, search, sort, bulk actions, manual deletion."""
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import require_auth
from ..db.utils import STATUS_PENDING, STATUS_DELETING, STATUS_DELETED, STATUS_ERROR, escape_like
from ..db.engine import get_db
from ..db.settings_store import get_setting
from ..db.logs import add_log
from ..db.repositories import (
    get_by_id, claim_for_deletion, update_queue_status,
    delete_by_id, delete_by_ids, purge_by_status,
    get_status_counts, get_all_for_enrichment, update_enrichment_fields,
    get_pending_for_poster_regen, update_poster,
)
from ..deletion import _delete_media
from ..rules.legacy_conditions import _get_poster_url
from ..arr_clients import seerr_find_request_by_tmdb

router = APIRouter(prefix="/api/media", tags=["media"])
logger = logging.getLogger(__name__)


# Explicit safe mapping — column names validated at definition time
_SORT_MAP: dict = {
    "title":          "title",
    "library_name":   "library_name",
    "library_id":     "library_id",
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
    counts = await get_status_counts()
    return {
        "pending": counts.get(STATUS_PENDING, 0),
        "deleted": counts.get(STATUS_DELETED, 0),
        "error": counts.get(STATUS_ERROR, 0),
        "total": sum(counts.values()),
    }


_VALID_STATUSES = frozenset({STATUS_PENDING, STATUS_DELETED, STATUS_ERROR})


@router.get("")
async def list_queue(
    user: str = Depends(require_auth),
    limit: int = Query(50, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    status: Optional[str] = None,
    search: Optional[str] = None,
    library_id: Optional[str] = None,
    sort: str = "delete_at",
    sort_dir: str = Query("asc", alias="dir"),
):
    if status and status not in _VALID_STATUSES:
        raise HTTPException(422, f"status invalide : doit être l'un de {sorted(_VALID_STATUSES)}")

    sort_col = _SORT_MAP.get(sort, "delete_at")  # safe mapping — never raw interpolation
    sort_dir = "DESC" if sort_dir.lower() == "desc" else "ASC"

    where = []
    params = []
    if status:
        where.append("status = ?")
        params.append(status)
    if library_id:
        where.append("library_id = ?")
        params.append(library_id)
    if search:
        where.append(
            "(title LIKE ? ESCAPE '!' OR library_name LIKE ? ESCAPE '!' "
            "OR seerr_username LIKE ? ESCAPE '!')"
        )
        s = f"%{escape_like(search)}%"
        params.extend([s, s, s])

    where_clause = f"WHERE {' AND '.join(where)}" if where else ""

    async with get_db() as db:
        count_row = await db.fetch_one(
            f"SELECT COUNT(*) AS cnt FROM media_queue {where_clause}", params
        )
        total = count_row["cnt"] if count_row else 0

        items = await db.fetch_all(
            f"SELECT * FROM media_queue {where_clause} "
            f"ORDER BY {sort_col} {sort_dir} LIMIT ? OFFSET ?",
            params + [limit, offset],
        )

    return {"items": items, "total": total}


@router.post("/{media_id}/delete-now")
async def delete_now(
    media_id: int, user: str = Depends(require_auth)
):
    """Manually trigger deletion of a single item."""
    # Dry-run simulates only: no claim, no status change — marking the item
    # 'deleted' here would remove it from the pipeline without deleting files.
    dry_run = (await get_setting("dry_run") or "false").lower() == "true"
    if dry_run:
        row = await get_by_id(media_id)
        if not row or row["status"] != STATUS_PENDING:
            raise HTTPException(404, "Média introuvable ou déjà supprimé")
        await _delete_media(row, True)
        return {"status": "dry_run"}

    # Atomically claim the item by updating status to 'deleting' — prevents double-delete race
    if not await claim_for_deletion(media_id):
        raise HTTPException(404, "Média introuvable ou déjà supprimé")
    row = await get_by_id(media_id)

    ok = await _delete_media(row, False)
    await update_queue_status(media_id, STATUS_DELETED if ok else STATUS_PENDING)
    if ok:
        return {"status": "deleted"}
    raise HTTPException(500, "Suppression échouée")


_UPSERT_IGNORED_SQL = (
    "INSERT OR REPLACE INTO ignored_media "
    "(emby_id, title, media_type, library_id, library_name, file_path, "
    "poster_url, tmdb_id, seerr_id, seerr_user_id, seerr_username, "
    "seerr_request_url, radarr_id, sonarr_id, added_date, last_played, "
    "reason, ignored_at, expire_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _upsert_ignored_params(row: dict, reason: str, ignored_at: str, expire_at) -> tuple:
    """Build the parameter tuple for _UPSERT_IGNORED_SQL from a media_queue row."""
    return (
        row["emby_id"], row["title"],
        row.get("media_type") or "Movie",
        row.get("library_id") or "", row.get("library_name") or "",
        row.get("file_path") or "", row.get("poster_url") or "",
        row.get("tmdb_id") or "", row.get("seerr_id"),
        row.get("seerr_user_id"), row.get("seerr_username") or "",
        row.get("seerr_request_url") or "",
        row.get("radarr_id"), row.get("sonarr_id"),
        row.get("added_date"), row.get("last_played"),
        reason, ignored_at, expire_at,
    )


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

        async with get_db() as db:
            rows = await db.fetch_all(
                f"SELECT * FROM media_queue WHERE id IN ({placeholders})",
                body.ids,
            )
            for row in rows:
                await db.execute(
                    _UPSERT_IGNORED_SQL,
                    _upsert_ignored_params(row, body.reason or "", ignored_at, expire_at),
                )
                await db.execute("DELETE FROM media_queue WHERE id=?", (row["id"],))
            await db.commit()
        return {"affected": len(rows)}

    if body.action == "delete":
        dry_run = (await get_setting("dry_run") or "false").lower() == "true"
        success = 0
        async with get_db() as db:
            rows = await db.fetch_all(
                f"SELECT * FROM media_queue WHERE id IN ({placeholders})",
                body.ids,
            )
        for row in rows:
            if await _delete_media(row, dry_run):
                await update_queue_status(row["id"], STATUS_DELETED)
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

    row = await get_by_id(media_id)
    if not row:
        raise HTTPException(404, "Média introuvable")
    async with get_db() as db:
        await db.execute(
            _UPSERT_IGNORED_SQL,
            _upsert_ignored_params(row, reason or "", datetime.now(timezone.utc).isoformat(), expire_at),
        )
        await db.execute("DELETE FROM media_queue WHERE id=?", (media_id,))
        await db.commit()
    return {"status": "ignored"}


@router.delete("/purge/deleted")
async def purge_deleted(user: str = Depends(require_auth)):
    """Remove all entries with status='deleted' from the queue."""
    count = await purge_by_status(STATUS_DELETED)
    await add_log("INFO", f"Purgé : {count} entrée(s) supprimée(s)", "system")
    return {"purged": count}


@router.post("/enrich-seerr")
async def enrich_seerr(
    background_tasks: BackgroundTasks, user: str = Depends(require_auth)
):
    """Re-fetch Seerr requester info AND regenerate poster URLs for all items."""
    async def _enrich():
        rows = await get_all_for_enrichment()
        enriched = 0
        for row in rows:
            updates = {}
            new_url = await _get_poster_url(
                row["emby_id"],
                tmdb_id=row.get("tmdb_id") or "",
                media_type=row.get("media_type") or "Movie",
                radarr_id=row.get("radarr_id"),
                sonarr_id=row.get("sonarr_id"),
            )
            if new_url:
                updates["poster_url"] = new_url
            if row.get("tmdb_id") and not row.get("seerr_username"):
                seerr_data = await seerr_find_request_by_tmdb(row["tmdb_id"])
                if seerr_data:
                    updates["seerr_id"] = seerr_data["seerr_id"]
                    updates["seerr_user_id"] = seerr_data["user_id"]
                    updates["seerr_username"] = seerr_data["username"]
                    ext = await get_setting("seerr_external_url")
                    if ext:
                        path = "movie" if row.get("media_type") == "Movie" else "tv"
                        updates["seerr_request_url"] = f"{ext.rstrip('/')}/{path}/{row['tmdb_id']}"
            if updates:
                await update_enrichment_fields(row["id"], updates)
                enriched += 1
        await add_log(
            "INFO", f"Enrichissement terminé : {enriched}/{len(rows)} entrées", "system"
        )

    background_tasks.add_task(_enrich)
    return {"status": "started"}


_REGEN_BATCH = 100  # max items processed per DB write to bound memory usage


@router.post("/regenerate-posters")
async def regenerate_posters(
    background_tasks: BackgroundTasks, user: str = Depends(require_auth)
):
    """Regenerate poster URLs for pending queue items (Radarr/Sonarr TMDB), in batches."""
    async def _regen():
        rows = await get_pending_for_poster_regen()
        total = len(rows)
        updated = 0
        for batch_start in range(0, total, _REGEN_BATCH):
            batch = rows[batch_start : batch_start + _REGEN_BATCH]
            for row in batch:
                new_url = await _get_poster_url(
                    row["emby_id"],
                    tmdb_id=row.get("tmdb_id") or "",
                    media_type=row.get("media_type") or "Movie",
                    radarr_id=row.get("radarr_id"),
                    sonarr_id=row.get("sonarr_id"),
                )
                if new_url:
                    await update_poster(row["id"], new_url)
                    updated += 1

        await add_log("INFO", f"Affiches régénérées : {updated}/{total}", "system")

    background_tasks.add_task(_regen)
    return {"status": "started"}


@router.delete("/{media_id}/remove")
@router.delete("/{media_id}")
async def remove_from_queue(media_id: int, user: str = Depends(require_auth)):
    """Remove an entry from the queue without deleting the media itself."""
    await delete_by_id(media_id)
    return {"status": "removed"}
