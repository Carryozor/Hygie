"""Backup — manual trigger + list endpoint."""
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_auth
from ..backup import _DEFAULT_PATH, list_backups, run_backup
from ..database import get_setting

router = APIRouter(prefix="/api/backup", tags=["backup"])


@router.post("")
async def trigger_backup(user: str = Depends(require_auth)):
    """Trigger a manual DB backup immediately."""
    filename = await run_backup()
    if filename is None:
        raise HTTPException(500, "Backup échoué")
    return {"filename": filename}


@router.get("")
async def get_backups(user: str = Depends(require_auth)):
    """List existing backup files."""
    backup_dir = (await get_setting("backup_path") or _DEFAULT_PATH).rstrip("/")
    return list_backups(backup_dir)


@router.delete("/{filename}")
async def delete_backup(filename: str, user: str = Depends(require_auth)):
    """Delete a backup file by name."""
    # Safety: only allow filenames matching expected pattern, no path traversal
    if "/" in filename or "\\" in filename or not filename.startswith("hygie_"):
        raise HTTPException(400, "Nom de fichier invalide")
    backup_dir = (await get_setting("backup_path") or _DEFAULT_PATH).rstrip("/")
    path = Path(backup_dir) / filename
    if not path.exists():
        raise HTTPException(404, "Fichier introuvable")
    os.unlink(path)
    return {"status": "deleted"}
