"""Settings — read/write configuration, test connections."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..auth import require_auth
from ..database import get_all_settings, set_setting, get_setting
from ..emby_client import test_connection as test_emby
from ..arr_clients import test_radarr, test_sonarr, test_seerr
from ..qbit_client import test_qbit
from ..discord_client import test_discord

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    """Partial settings update — all fields optional."""
    emby_url: Optional[str] = None
    emby_api_key: Optional[str] = None
    emby_external_url: Optional[str] = None
    emby_leaving_soon_collection: Optional[str] = None
    emby_leaving_soon_days: Optional[str] = None
    emby_leaving_soon_overlay: Optional[str] = None
    radarr_url: Optional[str] = None
    radarr_api_key: Optional[str] = None
    sonarr_url: Optional[str] = None
    sonarr_api_key: Optional[str] = None
    seerr_url: Optional[str] = None
    seerr_api_key: Optional[str] = None
    seerr_external_url: Optional[str] = None
    qbit_url: Optional[str] = None
    qbit_user: Optional[str] = None
    qbit_password: Optional[str] = None
    qbit_action: Optional[str] = None
    qbit_tag: Optional[str] = None
    discord_webhook: Optional[str] = None
    dry_run: Optional[str] = None
    scan_interval_hours: Optional[str] = None
    deletion_check_interval_hours: Optional[str] = None
    log_level: Optional[str] = None
    deleted_retention_days: Optional[str] = None
    log_retention_days: Optional[str] = None
    job_history_retention_days: Optional[str] = None
    ui_language: Optional[str] = None


_TESTERS = {
    "emby": test_emby,
    "radarr": test_radarr,
    "sonarr": test_sonarr,
    "seerr": test_seerr,
    "qbit": test_qbit,
    "discord": test_discord,
}


@router.get("")
async def get_settings(user: str = Depends(require_auth)):
    """Return all settings (sensitive fields masked for display)."""
    settings = await get_all_settings()
    return settings  # Return real values — frontend needs them for forms


@router.post("")
async def update_settings(body: SettingsUpdate, request: Request, user: str = Depends(require_auth)):
    """Update settings. Only sends non-None fields."""
    updated = []
    for key, value in body.model_dump(exclude_none=True).items():
        await set_setting(key, value)
        updated.append(key)

    # Reschedule les jobs si les intervalles ont changé
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler:
        if "scan_interval_hours" in updated:
            try:
                h = int(await get_setting("scan_interval_hours") or "6")
                scheduler.reschedule_job("scan_job", trigger="interval", hours=h)
            except Exception as e:
                pass  # Job pas encore créé ou autre erreur non-bloquante

        if "deletion_check_interval_hours" in updated:
            try:
                h = int(await get_setting("deletion_check_interval_hours") or "1")
                scheduler.reschedule_job("deletion_job", trigger="interval", hours=h)
            except Exception as e:
                pass

    return {"updated": updated}


@router.post("/test/{service}")
async def test_service(service: str, user: str = Depends(require_auth)):
    """Test a service connection."""
    tester = _TESTERS.get(service)
    if not tester:
        raise HTTPException(404, "Service inconnu")
    ok, message = await tester()
    return {"ok": ok, "message": message}
