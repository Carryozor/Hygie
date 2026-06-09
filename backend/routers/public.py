"""Public no-auth endpoint — upcoming deletions calendar."""
import hmac
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

from ..auth import verify_token
from ..db.settings_store import get_setting
from ..db.engine import get_db
from ..db.media_servers import get_media_servers
from ..db.encryption import _decrypt_value
from ..db.utils import parse_iso_dt

router = APIRouter(tags=["public"])


def _clean_url(raw: str) -> str:
    v = (raw or "").strip().rstrip("/")
    return _decrypt_value(v) if v.startswith("enc:") else v


@router.get("/api/public/upcoming")
async def public_upcoming(
    slug: str = "",
    password: str = "",
    authorization: Optional[str] = Header(default=None),
    x_dashboard_password: Optional[str] = Header(default=None),
):
    """No-auth endpoint — returns upcoming deletions if public_dashboard_enabled=true.

    Authenticated admins (valid Bearer token) bypass the public dashboard password.
    Anonymous visitors must supply the configured password if one is set.
    """
    enabled = await get_setting("public_dashboard_enabled")
    if enabled != "true":
        return JSONResponse({"error": "disabled"}, status_code=403)

    cfg_slug = (await get_setting("public_dashboard_slug") or "").strip()
    if cfg_slug and slug != cfg_slug:
        return JSONResponse({"error": "not_found"}, status_code=404)

    # Admins with a valid token bypass the public password requirement
    is_admin = False
    if authorization and authorization.startswith("Bearer "):
        is_admin = bool(verify_token(authorization[7:]))

    cfg_pwd = (await get_setting("public_dashboard_password") or "").strip()
    if cfg_pwd and not is_admin:
        provided = password or x_dashboard_password or ""
        if not provided:
            return JSONResponse({"error": "password_required"}, status_code=401)
        if not hmac.compare_digest(provided.encode(), cfg_pwd.encode()):
            return JSONResponse({"error": "wrong_password"}, status_code=403)

    async with get_db() as db:
        rows = await db.fetch_all(
            "SELECT mq.id, mq.emby_id, mq.title, mq.media_type, mq.library_name, mq.library_id, "
            "mq.delete_at, mq.poster_url, mq.seerr_username, mq.tmdb_id, mq.seerr_request_url, "
            "COALESCE(l.server_id, '0') AS server_id "
            "FROM media_queue mq "
            "LEFT JOIN libraries l ON mq.library_id = l.id "
            "WHERE mq.status='pending' ORDER BY mq.delete_at ASC"
        )
        libs = await db.fetch_all(
            "SELECT id, name, server_id FROM libraries WHERE enabled=1 ORDER BY name"
        )

    media_servers = await get_media_servers()
    safe_servers = [
        {
            "id":         str(s.get("id", "")),
            "name":       s.get("name") or "Serveur",
            "type":       s.get("type", ""),
            "ext_url":    _clean_url(s.get("ext_url", "") or s.get("url", "")),
            "server_uid": s.get("server_uid", ""),
        }
        for s in media_servers if s.get("enabled", True) is not False
    ]

    ui_language = await get_setting("ui_language") or "fr"

    grouped: dict = defaultdict(list)
    for d in rows:
        d = dict(d)
        dt = parse_iso_dt(d.get("delete_at"))
        if not dt:
            continue
        key = dt.strftime("%Y-%m-%d")
        grouped[key].append(d)

    return {
        "events": {date: items for date, items in sorted(grouped.items())},
        "libraries": [dict(r) for r in libs],
        "servers": safe_servers,
        "language": ui_language,
    }
