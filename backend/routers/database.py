"""Database management — info, connection test, migration."""
import asyncio
import logging
import os
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..auth import require_auth
from ..db.engine import get_db, DIALECT, SQLITE_PATH, DATABASE_URL
from ..db.logs import add_job_run, finish_job_run

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/database", tags=["database"])

_TABLES = [
    "settings", "users", "refresh_tokens", "libraries",
    "media_queue", "ignored_media", "seerr_user_rules",
    "logs", "job_history", "stats_history",
    "rate_limit", "expert_rules", "notifications",
]

_migration_lock = asyncio.Lock()


# ── Info ──────────────────────────────────────────────────────────────────────

@router.get("/info")
async def db_info(user: str = Depends(require_auth)):
    """Return current database type, connection string, and row counts per table."""
    if DIALECT == "mariadb":
        # Show host:port/db without credentials
        connection = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL
    else:
        connection = SQLITE_PATH

    counts = {}
    async with get_db() as db:
        for table in _TABLES:
            try:
                exists = await db.table_exists(table)
                if exists:
                    row = await db.fetch_one(f"SELECT COUNT(*) AS n FROM {table}")
                    counts[table] = row["n"] if row else 0
            except Exception:
                counts[table] = -1

    return {"dialect": DIALECT, "connection": connection, "tables": counts}


# ── Connection test ───────────────────────────────────────────────────────────

class TestRequest(BaseModel):
    url: str


@router.post("/test")
async def test_connection(body: TestRequest, user: str = Depends(require_auth)):
    """Test connectivity to a MariaDB URL."""
    url = body.url.strip()
    if not url:
        return {"ok": False, "message": "URL vide"}
    if not any(url.startswith(p) for p in ("mysql+", "mariadb+", "mysql://", "mariadb://")):
        return {"ok": False, "message": "L'URL doit commencer par mysql+aiomysql:// ou mariadb://"}
    try:
        import aiomysql
        from ..db.engine import _parse_mariadb_url
        kwargs = _parse_mariadb_url(url)
        conn = await asyncio.wait_for(
            aiomysql.connect(**kwargs, autocommit=True, charset="utf8mb4"),
            timeout=5.0,
        )
        try:
            async with conn.cursor() as cur:
                await cur.execute("SELECT VERSION()")
                row = await cur.fetchone()
                version = row[0] if row else "?"
        finally:
            conn.close()
        return {"ok": True, "message": f"Connexion réussie — MariaDB/MySQL {version}"}
    except asyncio.TimeoutError:
        return {"ok": False, "message": "Délai de connexion dépassé (5s)"}
    except ImportError:
        return {"ok": False, "message": "aiomysql non installé — MariaDB non supporté"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


# ── Migration ─────────────────────────────────────────────────────────────────

class MigrateRequest(BaseModel):
    direction: str           # "sqlite_to_mariadb" | "mariadb_to_sqlite"
    target_url: Optional[str] = None    # for sqlite_to_mariadb
    target_path: Optional[str] = None  # for mariadb_to_sqlite
    dry_run: bool = False


async def _run_migration(req: MigrateRequest, run_id: int) -> None:
    """Background migration task — tracked in job_history."""
    async with _migration_lock:
        try:
            if req.direction == "sqlite_to_mariadb":
                from ..tools.migrate_to_mariadb import migrate as do_migrate
                await do_migrate(
                    sqlite_path=SQLITE_PATH,
                    db_url=req.target_url,
                    dry_run=req.dry_run,
                )
                target_info = req.target_url.split("@")[-1] if req.target_url and "@" in req.target_url else req.target_url
                msg = f"{'[DRY RUN] ' if req.dry_run else ''}SQLite → MariaDB ({target_info}) OK"

            elif req.direction == "mariadb_to_sqlite":
                from ..tools.migrate_to_sqlite import migrate as do_migrate
                target = req.target_path or "/app/data/hygie_migrated.db"
                source_info = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL
                await do_migrate(
                    db_url=DATABASE_URL,
                    sqlite_path=target,
                    dry_run=req.dry_run,
                )
                msg = f"{'[DRY RUN] ' if req.dry_run else ''}MariaDB ({source_info}) → SQLite ({target}) OK"
            else:
                raise ValueError(f"Direction inconnue: {req.direction}")

            await finish_job_run(run_id, "success", msg)
            logger.info("Migration terminée : %s", msg)

        except Exception as e:
            logger.exception("Migration error")
            await finish_job_run(run_id, "error", str(e))


@router.post("/migrate")
async def start_migration(
    body: MigrateRequest,
    background_tasks: BackgroundTasks,
    user: str = Depends(require_auth),
):
    """Start a database migration as a background job."""
    if _migration_lock.locked():
        return JSONResponse({"status": "already_running"}, status_code=409)

    if body.direction == "sqlite_to_mariadb":
        if not body.target_url:
            return JSONResponse({"error": "target_url requis"}, status_code=422)
        if DIALECT == "mariadb":
            return JSONResponse({"error": "Hygie utilise déjà MariaDB"}, status_code=409)

    elif body.direction == "mariadb_to_sqlite":
        if DIALECT == "sqlite":
            return JSONResponse({"error": "Hygie utilise déjà SQLite"}, status_code=409)
    else:
        return JSONResponse({"error": "direction invalide"}, status_code=422)

    run_id = await add_job_run("db_migration")
    background_tasks.add_task(_run_migration, body, run_id)
    return {"status": "started", "job_id": run_id}


@router.get("/migrate/status")
async def migration_status(user: str = Depends(require_auth)):
    """Return the latest db_migration job status."""
    async with get_db() as db:
        row = await db.fetch_one(
            "SELECT id, started_at, finished_at, status, message "
            "FROM job_history WHERE job_type='db_migration' "
            "ORDER BY started_at DESC LIMIT 1"
        )
    if not row:
        return None
    return dict(row)
