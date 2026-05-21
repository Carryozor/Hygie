"""Libraries — CRUD operations + Emby library listing."""
import json
import uuid
from datetime import datetime, timezone
from typing import List, Literal, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

from ..auth import require_auth
from ..database import DB_PATH, add_log
from ..emby_client import get_libraries as emby_get_libraries
from ..scheduler import (
    is_scan_running,
    reevaluate_library_queue,
    run_scan_library,
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
    conditions: List[Condition] = []
    logic: Literal["AND", "OR"] = "AND"
    grace_days: int = Field(default=7, ge=0, le=3650)
    seerr_conditions: List[SeerrCondition] = []
    enabled: bool = True


class LibraryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    emby_library_id: Optional[str] = None
    conditions: Optional[List[Condition]] = None
    logic: Optional[Literal["AND", "OR"]] = None
    grace_days: Optional[int] = Field(default=None, ge=0, le=3650)
    seerr_conditions: Optional[List[SeerrCondition]] = None
    enabled: Optional[bool] = None


@router.get("/emby-libraries")
@router.get("/emby")
async def list_emby_libraries(user: str = Depends(require_auth)):
    """List available Emby libraries to choose from."""
    libs = await emby_get_libraries()
    return [{"id": lib["Id"], "name": lib["Name"]} for lib in libs]


@router.get("")
async def list_libraries(user: str = Depends(require_auth)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM libraries ORDER BY name"
        ) as cur:
            rows = await cur.fetchall()

    result = []
    for row in rows:
        d = dict(row)
        d["conditions"] = json.loads(d.get("conditions") or "[]")
        d["seerr_conditions"] = json.loads(d.get("seerr_conditions") or "[]")
        d["enabled"] = bool(d.get("enabled", 1))
        result.append(d)
    return result


@router.post("")
async def create_library(body: LibraryCreate, user: str = Depends(require_auth)):
    lib_id = str(uuid.uuid4())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO libraries
               (id, name, emby_library_id, conditions, logic, grace_days,
                seerr_conditions, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                lib_id,
                body.name,
                body.emby_library_id,
                json.dumps([c.model_dump() for c in body.conditions]),
                body.logic,
                body.grace_days,
                json.dumps([c.model_dump() for c in body.seerr_conditions]),
                int(body.enabled),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
    await add_log("INFO", f"Bibliothèque créée : {body.name}", "library")
    return {"id": lib_id}


@router.put("/{library_id}")
async def update_library(
    library_id: str,
    body: LibraryUpdate,
    background_tasks: BackgroundTasks,
    user: str = Depends(require_auth),
):
    _ALLOWED_LIB_COLS = frozenset({
        "name", "emby_library_id", "conditions", "logic",
        "grace_days", "seerr_conditions", "enabled",
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
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM libraries WHERE id=?", (library_id,))
        await db.commit()
    await add_log("INFO", f"Bibliothèque supprimée : {library_id}", "library")
    return {"status": "ok"}


@router.post("/{library_id}/clone")
async def clone_library(library_id: str, user: str = Depends(require_auth)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM libraries WHERE id=?", (library_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                raise HTTPException(404, "Bibliothèque introuvable")
            src = dict(row)

        new_id = str(uuid.uuid4())
        try:
            await db.execute(
                """INSERT INTO libraries
                   (id, name, emby_library_id, conditions, logic, grace_days,
                    seerr_conditions, enabled, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id,
                    f"{src['name']} (copie)",
                    src["emby_library_id"],
                    src["conditions"],
                    src["logic"],
                    src["grace_days"],
                    src["seerr_conditions"],
                    src["enabled"],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        except Exception:
            # Fallback without created_at for legacy DB
            await db.execute(
                """INSERT INTO libraries
                   (id, name, emby_library_id, conditions, logic, grace_days,
                    seerr_conditions, enabled)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id,
                    f"{src['name']} (copie)",
                    src["emby_library_id"],
                    src["conditions"],
                    src["logic"],
                    src["grace_days"],
                    src["seerr_conditions"],
                    src["enabled"],
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


@router.post("/{library_id}/reevaluate")
async def reevaluate(library_id: str, user: str = Depends(require_auth)):
    """Re-check pending items in this library against current conditions."""
    removed = await reevaluate_library_queue(library_id)
    return {"removed": removed}


@router.post("/test/{service}")
async def test_service_alias(service: str, user: str = Depends(require_auth)):
    """Test a service connection (alias of /api/settings/test/{service})."""
    from ..emby_client import test_connection as test_emby
    from ..arr_clients import test_radarr, test_sonarr, test_seerr
    from ..qbit_client import test_qbit
    from ..discord_client import test_discord
    testers = {
        "emby": test_emby,
        "radarr": test_radarr,
        "sonarr": test_sonarr,
        "seerr": test_seerr,
        "qbit": test_qbit,
        "discord": test_discord,
    }
    tester = testers.get(service)
    if not tester:
        raise HTTPException(404, "Service inconnu")
    result = await tester()
    ok, message = result[0], result[1]
    return {"ok": ok, "message": message}
