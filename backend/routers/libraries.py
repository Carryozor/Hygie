"""Libraries — CRUD operations + Emby library listing."""
import json
import uuid
from datetime import datetime, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

from ..auth import require_auth
from ..db.engine import get_db
from ..db.logs import add_log
from ..logmsg import lm

from ..emby_client import get_libraries as emby_get_libraries
from ..db.media_servers import is_plex as _is_plex
from ..scheduler import (
    is_scan_running,
    reevaluate_library_queue,
    run_scan_library,
    run_scan_libraries,
)

router = APIRouter(prefix="/api/libraries", tags=["libraries"])


class Condition(BaseModel):
    field: str
    op: Literal["gt", "gte", "lt", "lte", "eq"] = "gt"
    value: int = Field(default=0, ge=0)


class SeerrCondition(BaseModel):
    type: Literal["user_include", "user_exclude"]
    user_id: int
    username: str = ""


class LibraryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    emby_library_id: str
    server_id: str = "0"
    conditions: List[Condition] = []
    logic: Literal["AND", "OR"] = "AND"
    grace_days: int = Field(default=7, ge=0, le=3650)
    seerr_conditions: List[SeerrCondition] = []
    enabled: bool = True
    deletion_unit: Literal["episode", "season", "series"] = "episode"


class LibraryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    emby_library_id: Optional[str] = None
    server_id: Optional[str] = None
    conditions: Optional[List[Condition]] = None
    logic: Optional[Literal["AND", "OR"]] = None
    grace_days: Optional[int] = Field(default=None, ge=0, le=3650)
    seerr_conditions: Optional[List[SeerrCondition]] = None
    enabled: Optional[bool] = None
    deletion_unit: Optional[Literal["episode", "season", "series"]] = None


@router.get("/emby-libraries")
@router.get("/emby")
async def list_emby_libraries(server_id: str = "0", user: str = Depends(require_auth)):
    """List available Emby/Jellyfin libraries to choose from for the given server."""
    libs = await emby_get_libraries(server_id)
    return [{"id": lib["Id"], "name": lib["Name"]} for lib in libs]


@router.get("/plex/{server_id}/sections")
async def list_plex_sections(server_id: str, user: str = Depends(require_auth)):
    """List Plex library sections for a server, flagging already-configured ones."""
    from ..db.media_servers import get_media_servers
    from ..plex_client import build_plex_client
    servers = await get_media_servers()
    server = next((s for s in servers if str(s.get("id")) == server_id), None)
    if not server or not _is_plex(server):
        raise HTTPException(404, "Serveur Plex introuvable")
    plex = build_plex_client(server)
    if not plex:
        raise HTTPException(400, "Configuration Plex insuffisante (URL ou token manquant)")
    try:
        sections = await plex.get_libraries()
    except Exception as e:
        raise HTTPException(502, f"Erreur Plex : {e}")
    async with get_db() as db:
        existing = await db.fetch_all(
            "SELECT emby_library_id FROM libraries WHERE server_id=?", (server_id,)
        )
    configured = {r["emby_library_id"] for r in existing}
    return [{"id": s["id"], "title": s["title"], "type": s["type"], "configured": s["id"] in configured}
            for s in sections]


@router.get("")
async def list_libraries(user: str = Depends(require_auth)):
    async with get_db() as db:
        rows = await db.fetch_all("SELECT * FROM libraries ORDER BY name")

    result = []
    for d in rows:
        d = dict(d)
        d["conditions"] = json.loads(d.get("conditions") or "[]")
        d["seerr_conditions"] = json.loads(d.get("seerr_conditions") or "[]")
        d["enabled"] = bool(d.get("enabled", 1))
        result.append(d)
    return result


@router.post("")
async def create_library(body: LibraryCreate, user: str = Depends(require_auth)):
    lib_id = str(uuid.uuid4())
    async with get_db() as db:
        await db.execute(
            """INSERT INTO libraries
               (id, name, emby_library_id, server_id, conditions, logic, grace_days,
                seerr_conditions, enabled, deletion_unit, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                lib_id,
                body.name,
                body.emby_library_id,
                body.server_id,
                json.dumps([c.model_dump() for c in body.conditions]),
                body.logic,
                body.grace_days,
                json.dumps([c.model_dump() for c in body.seerr_conditions]),
                int(body.enabled),
                body.deletion_unit,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
    await add_log("INFO", lm("library.created", name=body.name), "library")
    return {"id": lib_id}


@router.put("/{library_id}")
async def update_library(
    library_id: str,
    body: LibraryUpdate,
    background_tasks: BackgroundTasks,
    user: str = Depends(require_auth),
):
    _ALLOWED_LIB_COLS = frozenset({
        "name", "emby_library_id", "server_id", "conditions", "logic",
        "grace_days", "seerr_conditions", "enabled", "deletion_unit",
    })
    updates = []
    params = []
    data = body.model_dump(exclude_none=True)

    if "conditions" in data:
        data["conditions"] = json.dumps([c if isinstance(c, dict) else c.model_dump() for c in data["conditions"]])
    if "seerr_conditions" in data:
        data["seerr_conditions"] = json.dumps([c if isinstance(c, dict) else c.model_dump() for c in data["seerr_conditions"]])
    if "enabled" in data:
        data["enabled"] = int(data["enabled"])

    for k, v in data.items():
        if k not in _ALLOWED_LIB_COLS:
            continue  # Silently skip unknown columns
        updates.append(f"{k}=?")
        params.append(v)

    if not updates:
        return {"status": "no_changes"}

    params.append(library_id)
    async with get_db() as db:
        await db.execute(
            f"UPDATE libraries SET {', '.join(updates)} WHERE id=?", params
        )
        await db.commit()

    # If conditions changed, reevaluate the queue in background
    if "conditions" in data or "logic" in data:
        background_tasks.add_task(reevaluate_library_queue, library_id)

    return {"status": "ok"}


@router.delete("/{library_id}")
async def delete_library(library_id: str, user: str = Depends(require_auth)):
    async with get_db() as db:
        await db.execute("DELETE FROM libraries WHERE id=?", (library_id,))
        await db.commit()
    await add_log("INFO", lm("library.deleted", id=library_id), "library")
    return {"status": "ok"}


@router.post("/{library_id}/clone")
async def clone_library(library_id: str, user: str = Depends(require_auth)):
    async with get_db() as db:
        src = await db.fetch_one(
            "SELECT * FROM libraries WHERE id=?", (library_id,)
        )
        if not src:
            raise HTTPException(404, "Bibliothèque introuvable")

        new_id = str(uuid.uuid4())
        await db.execute(
            """INSERT INTO libraries
               (id, name, emby_library_id, server_id, conditions, logic, grace_days,
                seerr_conditions, enabled, deletion_unit, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id,
                f"{src['name']} (copie)",
                src["emby_library_id"],
                src.get("server_id", "0"),
                src["conditions"],
                src["logic"],
                src["grace_days"],
                src["seerr_conditions"],
                src["enabled"],
                src.get("deletion_unit", "episode"),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
    return {"id": new_id}


@router.post("/{library_id}/scan")
async def scan_library_endpoint(
    library_id: str,
    background_tasks: BackgroundTasks,
    user: str = Depends(require_auth),
):
    """Trigger a scan for a single library."""
    if is_scan_running():
        raise HTTPException(409, "Un scan est déjà en cours")
    background_tasks.add_task(run_scan_library, library_id)
    return {"status": "started"}


class ScanMultiBody(BaseModel):
    library_ids: list


@router.post("/scan-multi")
async def scan_multi_endpoint(
    body: ScanMultiBody,
    background_tasks: BackgroundTasks,
    user: str = Depends(require_auth),
):
    """Scan multiple libraries sequentially in a single background task.

    Used by expert rules that target libraries on multiple servers (e.g. Emby + Plex).
    Avoids the race condition that occurs when making two separate /scan calls.
    """
    if is_scan_running():
        raise HTTPException(409, "Un scan est déjà en cours")
    ids = [str(lid) for lid in body.library_ids if lid]
    if not ids:
        return {"status": "nothing_to_scan"}

    # Use run_scan_libraries which holds a single lock for all libraries,
    # preventing the scheduled full scan from interrupting between scans.
    background_tasks.add_task(run_scan_libraries, ids)
    return {"status": "started", "library_ids": ids}


@router.post("/{library_id}/reevaluate")
async def reevaluate(library_id: str, user: str = Depends(require_auth)):
    """Re-check pending items in this library against current conditions."""
    removed = await reevaluate_library_queue(library_id)
    return {"removed": removed}


@router.post("/test/{service}")
async def test_service_alias(service: str, user: str = Depends(require_auth)):
    """Test a service connection (alias of /api/settings/test/{service})."""
    from .settings import _TESTERS
    tester = _TESTERS.get(service)
    if not tester:
        logger.warning("Test service inconnu : '%s'", service)
        raise HTTPException(404, f"Service inconnu : {service}")
    result = await tester()
    ok, message = result[0], result[1]
    if not ok:
        logger.warning("Test %s échoué : %s", service, message)
    return {"ok": ok, "message": message}
