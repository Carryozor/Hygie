"""
Discord notifications.

Public API:
  test_discord()                              — (bool, msg)
  send_notification(media_list, kind, dry)    — send embed(s)

Discord ID resolution priority for mentions:
  1. seerr_user_rules.discord_id (manually configured in Hygie)
  2. Seerr user notification settings discordId (auto from Seerr)
"""
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

import httpx

from .db.utils import DB_PATH, parse_iso_dt, http_retry
from .db.engine import get_db
from .db.settings_store import get_setting

logger = logging.getLogger(__name__)

_TITLES = {
    "detected": ("🔍 Nouveau média détecté",  0x57F287),
    "1d":       ("⚠️ Suppression dans 24h",   0xFF4500),
    "now":      ("🗑️ Médias supprimés",        0xFF0000),
}


def _get_kind_meta(kind: str) -> tuple:
    if kind in _TITLES:
        return _TITLES[kind]
    m = re.match(r'^(\d+)d$', kind)
    if m:
        days = int(m.group(1))
        label = "24 heures" if days == 1 else f"{days} jours"
        color = 0xFF4500 if days <= 2 else 0xF0A500 if days <= 7 else 0x6366F1
        return (f"📅 Suppression dans {label}", color)
    return ("ℹ️ Hygie", 0x6366F1)


async def _resolve_discord_id(seerr_user_id: Optional[int]) -> str:
    """
    Resolve Discord ID for a Seerr user.

    Priority:
      1. seerr_user_rules.discord_id  (manually set in Hygie UI)
      2. Seerr notification settings discordId  (auto-detected from Seerr)

    Returns the raw Discord ID string (without <@>), or '' if not found.
    """
    if not seerr_user_id:
        return ""

    # 1. Check Hygie's own mapping table
    try:
        async with get_db() as db:
            row = await db.fetch_one(
                "SELECT discord_id FROM seerr_user_rules "
                "WHERE CAST(seerr_user_id AS TEXT) = CAST(? AS TEXT) "
                "AND discord_id IS NOT NULL AND TRIM(discord_id) != '' "
                "ORDER BY CASE WHEN library_id='*' THEN 0 ELSE 1 END ASC "
                "LIMIT 1",
                (seerr_user_id,),
            )
            if row and row["discord_id"] and row["discord_id"].strip():
                return row["discord_id"].strip()
    except Exception as e:
        logger.debug(f"_resolve_discord_id DB lookup: {e}")

    # 2. Fallback: query Seerr notification settings directly
    try:
        seerr_url = (await get_setting("seerr_url") or "").rstrip("/")
        seerr_key = await get_setting("seerr_api_key") or ""
        if seerr_url and seerr_key:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(
                    f"{seerr_url}/api/v1/user/{seerr_user_id}/settings/notifications",
                    headers={"X-Api-Key": seerr_key},
                )
                if r.status_code == 200:
                    disc = str(r.json().get("discordId") or "").strip()
                    if disc:
                        logger.debug(
                            f"Discord ID for seerr_uid={seerr_user_id} "
                            f"found via Seerr: {disc}"
                        )
                        return disc
    except Exception as e:
        logger.debug(f"_resolve_discord_id Seerr lookup: {e}")

    return ""


async def _build_embed(m: dict, color: int, footer_label: str, kind: str, single: bool) -> dict:
    title = m.get("title") or "?"
    media_type = m.get("media_type") or "Movie"
    icon = "🎬" if media_type == "Movie" else "📺"
    lib_name = m.get("library_name") or "?"
    seerr_user_id = m.get("seerr_user_id")
    seerr_username = m.get("seerr_username") or ""
    poster = m.get("poster_url") or ""

    fields = [{"name": "📚 Bibliothèque", "value": lib_name, "inline": True}]

    if seerr_username:
        discord_id = await _resolve_discord_id(seerr_user_id)
        value = f"<@{discord_id}>" if discord_id else seerr_username
        fields.append({"name": "👤 Demandé par", "value": value, "inline": True})

    if kind != "now":
        delete_at = m.get("delete_at") or ""
        if delete_at:
            dt = parse_iso_dt(delete_at)
            if dt:
                days = max(0, (dt - datetime.now(timezone.utc)).days)
                date_str = dt.strftime("%d/%m/%Y")
                label = f"{date_str} (dans {days} jour{'s' if days > 1 else ''})"
                fields.append({"name": "📅 Suppression prévue", "value": label, "inline": True})

    embed: dict = {
        "title": f"{icon} {title}",
        "color": color,
        "fields": fields,
        "footer": {"text": f"Hygie • {footer_label}"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Image — public URLs only (Discord can't reach internal Docker hostnames)
    if poster and poster.startswith(("http://", "https://")):
        import ipaddress
        from urllib.parse import urlparse
        hostname = (urlparse(poster).hostname or "").lower()
        try:
            _ip = ipaddress.ip_address(hostname)
            is_internal = _ip.is_private or _ip.is_loopback
        except ValueError:
            is_internal = "." not in hostname or hostname in ("localhost",)
        if not is_internal:
            key = "image" if single else "thumbnail"
            embed[key] = {"url": poster}

    return embed


async def send_notification(media_list: List[dict], kind: str, dry_run: bool = False) -> bool:
    if dry_run:
        logger.info(f"[DRY RUN] Discord '{kind}' bloquée ({len(media_list)} médias)")
        return True
    if not media_list:
        return True

    webhook = await get_setting("discord_webhook")
    if not webhook:
        logger.info("Discord: webhook non configuré — notification ignorée")
        return False

    footer_label, color = _get_kind_meta(kind)
    single = len(media_list) == 1

    # Discord limits embeds to 10 per message — reserve last slot for overflow summary
    MAX_EMBEDS = 9 if len(media_list) > 9 else 10
    embeds: list = []
    for m in media_list[:MAX_EMBEDS]:
        try:
            embed = await _build_embed(m, color, footer_label, kind, single)
            embeds.append(embed)
        except Exception as e:
            logger.error(f"Embed error for '{m.get('title', '?')}': {e}")

    if not embeds:
        return False

    if len(media_list) > MAX_EMBEDS:
        overflow = len(media_list) - MAX_EMBEDS
        extras = ", ".join(m.get("title", "?") for m in media_list[MAX_EMBEDS:MAX_EMBEDS + 5])
        if overflow > 5:
            extras += f" et {overflow - 5} autre(s)"
        embeds.append({
            "title": f"… et {overflow} média(s) supplémentaire(s)",
            "description": extras,
            "color": color,
        })

    titles = [m.get("title", "?") for m in media_list]
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await http_retry(lambda: client.post(webhook, json={"embeds": embeds}))
            if r.status_code in (200, 204):
                logger.info(f"Discord '{kind}': notification envoyée ({len(media_list)} média(s))")
                return True
            logger.error(
                f"Discord webhook HTTP {r.status_code} pour '{kind}' "
                f"({', '.join(titles[:5])}{'…' if len(titles) > 5 else ''}): {r.text[:200]}"
            )
            return False
    except Exception as e:
        logger.error(
            f"Discord send exception pour '{kind}' "
            f"({', '.join(titles[:5])}{'…' if len(titles) > 5 else ''}): {e}"
        )
        return False


async def _get_alert_webhook() -> str:
    """Return the alerts webhook if configured, fall back to the main webhook."""
    return (
        await get_setting("discord_webhook_alerts")
        or await get_setting("discord_webhook")
        or ""
    )


async def send_alert(
    title: str,
    description: str,
    level: str = "error",
    mention: str = "",
    custom_msg: str = "",
    template_vars: Optional[dict] = None,
) -> bool:
    """Send a critical operational alert to the alerts webhook (or main webhook as fallback).

    mention: Discord mention string (@here, <@ID>, <@&ROLE>) — goes in content field to actually ping.
    custom_msg: template string; supports {detail}, {title}, {count} vars via template_vars.
    """
    webhook = await _get_alert_webhook()
    if not webhook:
        return False

    colors = {"error": 0xFF0000, "warning": 0xF0A500, "info": 0x6366F1}
    icons  = {"error": "🚨", "warning": "⚠️", "info": "ℹ️"}

    body_text = description
    if custom_msg:
        try:
            body_text = custom_msg.format(**(template_vars or {}))
        except (KeyError, ValueError):
            body_text = custom_msg  # use as-is if template vars don't match

    payload: dict = {
        "embeds": [{
            "title": f"{icons.get(level, '🚨')} {title}",
            "description": body_text,
            "color": colors.get(level, 0xFF0000),
            "footer": {"text": "Hygie — Alerte"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]
    }
    # content field triggers actual Discord pings (@here, @everyone, <@ID>, <@&ROLE>)
    if mention and mention.strip():
        payload["content"] = mention.strip()

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(webhook, json=payload)
            return r.status_code in (200, 204)
    except Exception as e:
        logger.error(f"send_alert error: {e}")
        return False


async def _test_webhook(webhook: str, label: str) -> tuple[bool, str]:
    payload = {"embeds": [{
        "title": f"✅ Hygie — Test {label}",
        "description": f"Le webhook **{label}** fonctionne correctement.",
        "color": 0x6366F1,
        "footer": {"text": "Hygie"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(webhook, json=payload)
            if r.status_code in (200, 204):
                return True, f"Message de test envoyé ✅ ({label})"
            return False, f"HTTP {r.status_code}: {r.text[:100]}"
    except Exception as e:
        return False, str(e)


async def test_discord() -> tuple[bool, str]:
    webhook = await get_setting("discord_webhook")
    if not webhook:
        return False, "Webhook notifications non configuré"
    return await _test_webhook(webhook, "notifications")


async def test_discord_alerts() -> tuple[bool, str]:
    webhook = await get_setting("discord_webhook_alerts")
    if not webhook:
        return False, "Webhook alertes non configuré"
    return await _test_webhook(webhook, "alertes")
