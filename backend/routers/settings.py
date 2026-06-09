"""Settings — read/write configuration, test connections."""
import json
import logging
from typing import Optional
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..auth import require_auth

logger = logging.getLogger(__name__)
from ..db.settings_store import get_all_settings, set_setting, get_setting
from ..db.media_servers import get_media_servers, save_media_servers
from ..db.encryption import SENSITIVE_KEYS
from ..emby_client import test_connection as test_emby
from ..db.media_servers import is_plex
from ..arr_clients import test_radarr, test_sonarr, test_seerr
from ..qbit_client import test_qbit, test_qui
from ..discord_client import test_discord, test_discord_alerts
from ..services.arr_service import test_arr_instance as _test_arr, sync_arr_from_seerr as _sync_arr

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    """Partial settings update — all fields optional."""
    emby_leaving_soon_collection: Optional[str] = None
    emby_leaving_soon_days: Optional[str] = None
    emby_leaving_soon_overlay: Optional[str] = None
    radarr_url: Optional[str] = None
    radarr_api_key: Optional[str] = None
    radarr_servers: Optional[str] = None
    sonarr_url: Optional[str] = None
    sonarr_api_key: Optional[str] = None
    sonarr_servers: Optional[str] = None
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
    discord_alert_deletion_error_mention: Optional[str] = None
    discord_alert_deletion_error_msg: Optional[str] = None
    discord_alert_scan_failure: Optional[str] = None
    discord_alert_scan_failure_mention: Optional[str] = None
    discord_alert_scan_failure_msg: Optional[str] = None
    discord_alert_seerr_failure: Optional[str] = None
    discord_alert_seerr_failure_mention: Optional[str] = None
    discord_alert_seerr_failure_msg: Optional[str] = None
    discord_alert_error_threshold: Optional[str] = None
    max_parallel_library_scans: Optional[str] = None
    dry_run: Optional[str] = None
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
    backup_enabled: Optional[str] = None
    plex_tv_token: Optional[str] = None
    plex_webhook_secret: Optional[str] = None
    plex_overlay_enabled: Optional[str] = None
    public_dashboard_enabled: Optional[str] = None
    public_dashboard_slug:    Optional[str] = None
    public_dashboard_password: Optional[str] = None


_TESTERS = {
    "emby": test_emby,
    "radarr": test_radarr,
    "sonarr": test_sonarr,
    "seerr": test_seerr,
    "qbit": test_qbit,
    "qui": test_qui,
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
        if key in ("media_servers", "radarr_servers", "sonarr_servers"):
            # JSON array — mask api_key inside each server object
            try:
                servers = json.loads(settings[key])
                for s in servers:
                    if s.get("api_key"):
                        s["api_key"] = _MASK
                settings[key] = json.dumps(servers)
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
    # Skip masked values (user didn't change the field, backend will ignore them anyway)
    for key in _URL_SETTINGS:
        value = incoming.get(key, "") or ""
        if not value or (key in SENSITIVE_KEYS and value == _MASK):
            continue
        if urlparse(value).scheme.lower() not in ("http", "https"):
            raise HTTPException(status_code=422, detail=f"{key}: le schéma doit être http ou https")

    # Read current interval values BEFORE saving to detect real changes
    old_scan = await get_setting("scan_interval_minutes") or "360"
    old_del  = await get_setting("deletion_check_interval_minutes") or "60"

    # Restore masked api_keys inside radarr_servers / sonarr_servers JSON arrays
    for arr_key in ("radarr_servers", "sonarr_servers"):
        if arr_key not in incoming:
            continue
        try:
            new_arr = json.loads(incoming[arr_key] or "[]")
            existing_raw = await get_setting(arr_key) or "[]"
            existing_by_id = {s.get("id"): s for s in (json.loads(existing_raw) or [])}
            for s in new_arr:
                if s.get("api_key") == _MASK:
                    existing = existing_by_id.get(s.get("id")) or {}
                    s["api_key"] = existing.get("api_key", "")
            incoming[arr_key] = json.dumps(new_arr)
        except Exception:
            pass

    updated = []
    for key, value in incoming.items():
        # Skip masked values — user didn't change the field
        if key in SENSITIVE_KEYS and value == _MASK:
            continue
        await set_setting(key, value)
        updated.append(key)

    # Reschedule scan/deletion only if the interval value actually changed
    scan_min: int | None = None
    del_min: int | None = None
    if "scan_interval_minutes" in updated:
        new_scan = incoming["scan_interval_minutes"]
        if str(new_scan) != str(old_scan):
            try:
                scan_min = max(1, min(10080, int(new_scan)))
            except (ValueError, TypeError):
                pass
    if "deletion_check_interval_minutes" in updated:
        new_del = incoming["deletion_check_interval_minutes"]
        if str(new_del) != str(old_del):
            try:
                del_min = max(1, min(10080, int(new_del)))
            except (ValueError, TypeError):
                pass
    if scan_min is not None or del_min is not None:
        from .._scheduler_instance import reschedule_jobs
        reschedule_jobs(scan_minutes=scan_min, deletion_minutes=del_min)

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler:
        if "backup_interval_hours" in updated or "backup_enabled" in updated:
            try:
                from ..backup import run_backup as _run_backup, _DEFAULT_INTERVAL_HOURS
                from ..db.settings_store import get_bool_setting as _get_bool, get_int_setting as _get_int
                hours = int(incoming.get("backup_interval_hours") or await _get_int("backup_interval_hours", _DEFAULT_INTERVAL_HOURS))
                enabled = (incoming.get("backup_enabled", "true") == "true")
                if not enabled or hours <= 0:
                    try:
                        scheduler.remove_job("backup_job")
                    except Exception:
                        pass
                else:
                    scheduler.add_job(
                        _run_backup, "interval", hours=hours,
                        id="backup_job", replace_existing=True,
                    )
            except (ValueError, TypeError, Exception):
                pass

    # Invalidate image proxy whitelist when service URLs change
    _url_keys = {"emby_url", "emby_external_url", "radarr_url", "sonarr_url"}
    if _url_keys & set(updated):
        try:
            from ..proxy import invalidate_proxy_whitelist
            invalidate_proxy_whitelist()
        except Exception:
            pass

    return {"updated": updated}


# ─── Media servers CRUD ────────────────────────────────────────────────────────

class MediaServerBody(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    api_key: Optional[str] = None
    ext_url: Optional[str] = None
    type: Optional[str] = None
    enabled: Optional[bool] = None


def _validate_server_url(value: Optional[str], field: str) -> str:
    """Validate a server URL field.

    None  → field absent in PATCH request, return "" (no change to existing value).
    ""    → field explicitly set to empty string, rejected with 422.
    "ftp" → wrong scheme, rejected with 422.
    """
    if value is None:
        return ""
    val = value.strip()
    if not val:
        raise HTTPException(
            status_code=422,
            detail=f"{field}: l'URL ne peut pas être vide",
        )
    if urlparse(val).scheme.lower() not in ("http", "https"):
        raise HTTPException(status_code=422, detail=f"{field}: le schéma doit être http ou https")
    return val.rstrip("/")


@router.get("/media-servers")
async def list_media_servers(user: str = Depends(require_auth)):
    servers = await get_media_servers()
    return servers


@router.post("/media-servers", status_code=201)
async def add_media_server(body: MediaServerBody, user: str = Depends(require_auth)):
    # POST requires a non-empty URL — treat None (field absent) as empty string
    url = _validate_server_url(body.url or "", "url")
    ext_url = _validate_server_url(body.ext_url, "ext_url")
    servers = await get_media_servers()
    new_id = str(max([int(s.get("id", 0)) for s in servers], default=-1) + 1)
    servers.append({
        "id": new_id,
        "name": body.name or f"Serveur {new_id}",
        "url": url,
        "api_key": body.api_key or "",
        "ext_url": ext_url,
        "type": body.type or "",
        "enabled": body.enabled if body.enabled is not None else True,
    })
    await save_media_servers(servers)
    return {"id": new_id, "servers": servers}


@router.put("/media-servers/{server_id}")
async def update_media_server(server_id: str, body: MediaServerBody, user: str = Depends(require_auth)):
    servers = await get_media_servers()
    for s in servers:
        if str(s.get("id")) == server_id:
            if body.name is not None:
                s["name"] = body.name
            if body.url is not None:
                s["url"] = _validate_server_url(body.url, "url")
            if body.api_key is not None and body.api_key != _MASK:
                s["api_key"] = body.api_key
            if body.ext_url is not None:
                s["ext_url"] = _validate_server_url(body.ext_url, "ext_url")
            if body.type is not None:
                s["type"] = body.type
            if body.enabled is not None:
                s["enabled"] = body.enabled
    await save_media_servers(servers)

    # When a server is disabled, purge its pending deletion queue items —
    # those items would never be deleted anyway since the server is offline.
    if body.enabled is False:
        await _purge_server_queue(server_id)

    return {"servers": servers}


@router.delete("/media-servers/{server_id}")
async def delete_media_server(server_id: str, user: str = Depends(require_auth)):
    servers = await get_media_servers()
    servers = [s for s in servers if str(s.get("id")) != server_id]
    await save_media_servers(servers)
    # Also purge pending queue items for the deleted server
    await _purge_server_queue(server_id)
    return {"servers": servers}


async def _purge_server_queue(server_id: str) -> int:
    """Delete pending media_queue entries from all libraries of a given server.

    Called when a server is disabled or removed. Returns the number of rows deleted.
    """
    from ..db.engine import get_db
    from ..db.logs import add_log
    async with get_db() as db:
        rows = await db.fetch_all(
            "SELECT mq.id FROM media_queue mq "
            "JOIN libraries l ON mq.library_id = l.id "
            "WHERE l.server_id = ? AND mq.status = 'pending'",
            (server_id,),
        )
        if not rows:
            return 0
        ids = [r["id"] for r in rows]
        ph  = ",".join("?" * len(ids))
        await db.execute_write(
            f"DELETE FROM media_queue WHERE id IN ({ph})", tuple(ids)
        )
        await db.commit()
    await add_log(
        "INFO",
        f"Serveur {server_id} désactivé/supprimé : {len(ids)} entrée(s) retirée(s) de la file d'attente",
        "system",
    )
    return len(ids)


@router.post("/media-servers/{server_id}/purge-queue")
async def purge_server_queue_endpoint(server_id: str, user: str = Depends(require_auth)):
    """Manually purge all pending deletion queue items for a given server.

    Useful after disabling a server to immediately clean up leftover entries.
    """
    count = await _purge_server_queue(server_id)
    return {"purged": count}


@router.post("/media-servers/{server_id}/test")
async def test_media_server(server_id: str, user: str = Depends(require_auth)):
    servers = await get_media_servers()
    server = next((s for s in servers if str(s.get("id")) == server_id), None)
    if server and is_plex(server):
        from ..plex_client import test_plex_server
        result = await test_plex_server(server)
    else:
        result = await test_emby(server_id=server_id)
    ok, message, server_type = result[0], result[1], result[2]
    error_code = result[3] if len(result) > 3 else ""
    return {"ok": ok, "message": message, "server_type": server_type, "error_code": error_code}


@router.get("/reveal/{key}")
async def reveal_setting(key: str, user: str = Depends(require_auth)):
    """Return the plaintext value of a sensitive setting for display in the UI."""
    if key not in SENSITIVE_KEYS:
        raise HTTPException(status_code=403, detail="Clé non révélable")
    value = await get_setting(key)
    if not value:
        return {"value": ""}
    if key in ("media_servers", "radarr_servers", "sonarr_servers"):
        try:
            return {"value": json.loads(value)}
        except Exception:
            return {"value": value}
    return {"value": value}


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


class ArrTestRequest(BaseModel):
    type: str   # "radarr" | "sonarr"
    url: str
    api_key: str


@router.post("/test-arr")
async def test_arr_instance(body: ArrTestRequest, user: str = Depends(require_auth)):
    """Test a specific Radarr/Sonarr instance by URL and API key."""
    _validate_server_url(body.url or "", "url")
    return await _test_arr(body.type, body.url, body.api_key)


class SeerrSyncRequest(BaseModel):
    seerr_url: str
    seerr_api_key: str


@router.post("/sync-arr-from-seerr")
async def sync_arr_from_seerr(body: SeerrSyncRequest, user: str = Depends(require_auth)):
    """Import Radarr/Sonarr instances from Seerr configuration and merge with existing ones."""
    _validate_server_url(body.seerr_url or "", "seerr_url")
    try:
        return await _sync_arr(body.seerr_url, body.seerr_api_key)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Impossible de contacter Seerr : {e}")
