"""Settings — read/write configuration, test connections."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import require_auth
from ..database import get_all_settings, set_setting
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
    # Mask sensitive fields for display
    masked = dict(settings)
    for k in ("emby_api_key", "radarr_api_key", "sonarr_api_key",
              "seerr_api_key", "qbit_password", "discord_webhook"):
        if masked.get(k):
            masked[k] = "***" + masked[k][-4:] if len(masked[k]) > 4 else "***"
    return settings  # Return real values — frontend needs them for forms


@router.post("")
async def update_settings(body: SettingsUpdate, user: str = Depends(require_auth)):
    """Update settings. Only sends non-None fields."""
    updated = []
    for key, value in body.model_dump(exclude_none=True).items():
        await set_setting(key, value)
        updated.append(key)
    return {"updated": updated}


@router.post("/test/{service}")
async def test_service(service: str, user: str = Depends(require_auth)):
    """Test a service connection."""
    tester = _TESTERS.get(service)
    if not tester:
        raise HTTPException(404, "Service inconnu")
    ok, message = await tester()
    return {"ok": ok, "message": message}
