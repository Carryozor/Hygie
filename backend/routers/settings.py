"""Settings — read/write configuration, test connections."""
from typing import Optional
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..auth import require_auth
import json
from ..database import get_all_settings, set_setting, get_setting, get_media_servers, save_media_servers, SENSITIVE_KEYS
from ..emby_client import test_connection as test_emby
from ..arr_clients import test_radarr, test_sonarr, test_seerr
from ..qbit_client import test_qbit
from ..discord_client import test_discord, test_discord_alerts

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    """Partial settings update — all fields optional."""
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
    qbit_proxy_url: Optional[str] = None
    qbit_user: Optional[str] = None
    qbit_password: Optional[str] = None
    qbit_action: Optional[str] = None
    qbit_tag: Optional[str] = None
    discord_webhook: Optional[str] = None
    discord_webhook_alerts: Optional[str] = None
    discord_notif_thresholds: Optional[str] = None
    discord_alert_deletion_error: Optional[str] = None
    discord_alert_scan_failure: Optional[str] = None
    discord_alert_seerr_failure: Optional[str] = None
    discord_alert_error_threshold: Optional[str] = None
    max_parallel_library_scans: Optional[str] = None
    dry_run: Optional[str] = None
    scan_interval_hours: Optional[str] = None
    deletion_check_interval_hours: Optional[str] = None
    log_level: Optional[str] = None
    deleted_retention_days: Optional[str] = None
    log_retention_days: Optional[str] = None
    job_history_retention_days: Optional[str] = None
    scan_interval_minutes: Optional[str] = None
    deletion_check_interval_minutes: Optional[str] = None
    media_server_type: Optional[str] = None
    ui_language: Optional[str] = None
    backup_path: Optional[str] = None
    backup_interval_hours: Optional[str] = None
    backup_retention_count: Optional[str] = None


_TESTERS = {
    "emby": test_emby,
    "radarr": test_radarr,
    "sonarr": test_sonarr,
    "seerr": test_seerr,
    "qbit": test_qbit,
    "discord": test_discord,
    "discord_alerts": test_discord_alerts,
}


_MASK = "***"

@router.get("")
async def get_settings(user: str = Depends(require_auth)):
    """Return all settings. Sensitive fields that are set are masked with '***'."""
    settings = await get_all_settings()
    for key in SENSITIVE_KEYS:
        if key not in settings or not settings[key]:
            continue
        if key == "media_servers":
            # media_servers is a JSON array — mask api_key inside each server object
            import json as _json
            try:
                servers = _json.loads(settings[key])
                for s in servers:
                    if s.get("api_key"):
                        s["api_key"] = _MASK
                settings[key] = _json.dumps(servers)
            except Exception:
                settings[key] = _MASK
        else:
            settings[key] = _MASK
    return settings


_URL_SETTINGS = frozenset({
    "radarr_url", "sonarr_url", "seerr_url", "seerr_external_url",
    "qbit_url", "qbit_proxy_url",
})


@router.post("")
async def update_settings(body: SettingsUpdate, request: Request, user: str = Depends(require_auth)):
    """Update settings. Only sends non-None fields."""
    incoming = body.model_dump(exclude_none=True)

    # Validate URL schemes — reject file://, ftp://, etc. to prevent SSRF
    for key in _URL_SETTINGS:
        value = incoming.get(key, "") or ""
        if value and urlparse(value).scheme.lower() not in ("http", "https"):
            raise HTTPException(status_code=422, detail=f"{key}: le schéma doit être http ou https")

    # Read current interval values BEFORE saving to detect real changes
    old_scan = await get_setting("scan_interval_minutes") or "360"
    old_del  = await get_setting("deletion_check_interval_minutes") or "60"

    updated = []
    for key, value in incoming.items():
        # Skip masked values — user didn't change the field
        if key in SENSITIVE_KEYS and value == _MASK:
            continue
        await set_setting(key, value)
        updated.append(key)

    # Reschedule only if the interval value actually changed — prevents timer reset on unrelated saves
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler:
        if "scan_interval_minutes" in updated:
            new_scan = incoming["scan_interval_minutes"]
            if str(new_scan) != str(old_scan):
                try:
                    minutes = max(1, min(10080, int(new_scan)))
                    scheduler.reschedule_job("scan_job", trigger="interval", minutes=minutes)
                except (ValueError, TypeError):
                    pass
                except Exception:
                    pass
        if "deletion_check_interval_minutes" in updated:
            new_del = incoming["deletion_check_interval_minutes"]
            if str(new_del) != str(old_del):
                try:
                    minutes = max(1, min(10080, int(new_del)))
                    scheduler.reschedule_job("deletion_job", trigger="interval", minutes=minutes)
                except (ValueError, TypeError):
                    pass
                except Exception:
                    pass

    # Invalidate image proxy whitelist when service URLs change
    _url_keys = {"emby_url", "emby_external_url", "radarr_url", "sonarr_url"}
    if _url_keys & set(updated):
        try:
            from ..main import invalidate_proxy_whitelist
            invalidate_proxy_whitelist()
        except Exception:
            pass

    return {"updated": updated}


# ─── Media servers CRUD ────────────────────────────────────────────────────────

@router.get("/media-servers")
async def list_media_servers(user: str = Depends(require_auth)):
    servers = await get_media_servers()
    return servers


@router.post("/media-servers")
async def add_media_server(body: dict, user: str = Depends(require_auth)):
    servers = await get_media_servers()
    new_id = str(max([int(s.get("id", 0)) for s in servers], default=-1) + 1)
    servers.append({
        "id": new_id,
        "name": body.get("name") or f"Serveur {new_id}",
        "url": (body.get("url") or "").rstrip("/"),
        "api_key": body.get("api_key") or "",
        "ext_url": (body.get("ext_url") or "").rstrip("/"),
        "type": "",
        "enabled": body.get("enabled", True),
    })
    await save_media_servers(servers)
    return {"id": new_id, "servers": servers}


@router.put("/media-servers/{server_id}")
async def update_media_server(server_id: str, body: dict, user: str = Depends(require_auth)):
    servers = await get_media_servers()
    for s in servers:
        if str(s.get("id")) == server_id:
            for k in ("name", "url", "api_key", "ext_url", "enabled"):
                if k in body:
                    val = body[k]
                    if k == "api_key" and val == _MASK:
                        continue  # user didn't change the key
                    if k in ("url", "ext_url") and isinstance(val, str):
                        val = val.rstrip("/")
                    s[k] = val
    await save_media_servers(servers)
    return {"servers": servers}


@router.delete("/media-servers/{server_id}")
async def delete_media_server(server_id: str, user: str = Depends(require_auth)):
    servers = await get_media_servers()
    servers = [s for s in servers if str(s.get("id")) != server_id]
    await save_media_servers(servers)
    return {"servers": servers}


@router.post("/media-servers/{server_id}/test")
async def test_media_server(server_id: str, user: str = Depends(require_auth)):
    ok, message, server_type = await test_emby(server_id=server_id)
    return {"ok": ok, "message": message, "server_type": server_type}


@router.post("/test/{service}")
async def test_service(service: str, user: str = Depends(require_auth)):
    """Test a service connection."""
    tester = _TESTERS.get(service)
    if not tester:
        raise HTTPException(404, "Service inconnu")
    result = await tester()
    # test_connection returns (bool, str, str); others return (bool, str)
    ok, message = result[0], result[1]
    return {"ok": ok, "message": message}
