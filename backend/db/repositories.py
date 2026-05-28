"""Database query functions — single source of truth for media_queue and libraries SQL."""
import aiosqlite

from .utils import now_utc


async def get_pending_queue(*, db_path: str) -> list[dict]:
    """Return pending media_queue rows whose delete_at has passed."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM media_queue WHERE status='pending' AND delete_at <= ?",
            (now_utc().isoformat(),),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_queued_and_ignored_ids(*, db_path: str) -> tuple[set, set]:
    """Return (queued_emby_ids, ignored_emby_ids) from a single DB connection."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT emby_id FROM media_queue") as cur:
            queued = {r[0] async for r in cur}
        async with db.execute("SELECT emby_id FROM ignored_media") as cur:
            ignored = {r[0] async for r in cur}
    return queued, ignored


async def get_enabled_libraries(server_id: str, *, db_path: str) -> list[dict]:
    """Return enabled library rows for the given server_id."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM libraries"
            " WHERE enabled=1 AND (server_id=? OR server_id IS NULL OR server_id='')",
            (server_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def insert_queue_entry(entry: dict, *, db_path: str) -> None:
    """Insert one row into media_queue (status='pending')."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO media_queue
            (emby_id, title, media_type, library_id, library_name, file_path,
             poster_url, tmdb_id, seerr_id, seerr_user_id, seerr_username,
             seerr_request_url, radarr_id, sonarr_id, sonarr_series_id, season_number,
             detected_at, delete_at, added_date, last_played, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (
                entry["emby_id"], entry["title"], entry["media_type"],
                entry["library_id"], entry["library_name"], entry["file_path"],
                entry["poster_url"], entry["tmdb_id"],
                entry["seerr_id"], entry["seerr_user_id"], entry["seerr_username"],
                entry["seerr_request_url"], entry["radarr_id"], entry["sonarr_id"],
                entry.get("sonarr_series_id"), entry.get("season_number"),
                entry["detected_at"], entry["delete_at"],
                entry["added_date"], entry["last_played"],
            ),
        )
        await db.commit()


async def mark_notified_detected(emby_id: str, *, db_path: str) -> None:
    """Set notified_detected=1 for the pending queue row with this emby_id."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE media_queue SET notified_detected=1 WHERE emby_id=? AND status='pending'",
            (emby_id,),
        )
        await db.commit()


async def update_queue_status(item_id: int, status: str, *, db_path: str) -> None:
    """Update status and set notified_now=1 for a media_queue row."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE media_queue SET status=?, notified_now=1 WHERE id=?",
            (status, item_id),
        )
        await db.commit()
