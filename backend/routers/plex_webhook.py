# backend/routers/plex_webhook.py
"""POST /api/plex/webhook — receive Plex Media Server webhook events."""
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Query, Response

from ..db.engine import get_db
from ..db.settings_store import get_setting

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/plex", tags=["plex"])

_HANDLED_EVENTS = {"media.scrobble", "media.play", "media.pause", "media.resume", "media.stop"}


@router.post("/webhook")
async def plex_webhook(
    payload: str = Form(...),
    secret: str = Query(default=""),
) -> Response:
    """Receive a Plex webhook event (multipart/form-data, payload field = JSON)."""
    configured_secret = await get_setting("plex_webhook_secret") or ""
    if configured_secret and secret != configured_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event = data.get("event", "")
    if event not in _HANDLED_EVENTS:
        logger.debug("Plex webhook: ignoring event %s", event)
        return Response(status_code=200)

    metadata = data.get("Metadata") or {}
    rating_key = str(metadata.get("ratingKey", ""))
    title = metadata.get("title", "")
    account = data.get("Account") or {}
    username = account.get("title", "unknown")

    logger.info("Plex webhook: %s — %r (user=%s, ratingKey=%s)", event, title, username, rating_key)

    if event == "media.scrobble" and rating_key:
        await _handle_scrobble(rating_key, metadata)

    return Response(status_code=200)


async def _handle_scrobble(rating_key: str, metadata: dict) -> None:
    last_viewed_at_ts = metadata.get("lastViewedAt")
    if last_viewed_at_ts:
        last_played = datetime.fromtimestamp(last_viewed_at_ts, tz=timezone.utc).isoformat()
    else:
        last_played = datetime.now(timezone.utc).isoformat()

    async with get_db() as db:
        await db.execute_write(
            "UPDATE media_queue SET last_played=?, status='pending' WHERE emby_id=?",
            (last_played, rating_key),
        )
        await db.commit()
    logger.debug("Plex scrobble: updated last_played for ratingKey=%s", rating_key)
