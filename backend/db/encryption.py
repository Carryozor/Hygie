"""Fernet encryption helpers for sensitive settings."""
import logging
import os

logger = logging.getLogger(__name__)

_ENC_PREFIX = "enc:"

SENSITIVE_KEYS = frozenset({
    "emby_api_key",
    "radarr_api_key",
    "sonarr_api_key",
    "seerr_api_key",
    "qbit_password",
    "qbit_proxy_url",
    "discord_webhook",
    "discord_webhook_alerts",
    "media_servers",    # JSON array — encrypt the whole blob
    "radarr_servers",   # JSON array — encrypt the whole blob
    "sonarr_servers",   # JSON array — encrypt the whole blob
    "plex_tv_token",
    "plex_webhook_secret",
})

_fernet_instance = None
_fernet_loaded   = False


def _get_fernet():
    """Return a Fernet instance if HYGIE_ENCRYPTION_KEY is configured, else None."""
    global _fernet_instance, _fernet_loaded
    if _fernet_loaded:
        return _fernet_instance
    _fernet_loaded = True
    raw_key = os.environ.get("HYGIE_ENCRYPTION_KEY", "").strip()
    if not raw_key:
        return None
    try:
        from cryptography.fernet import Fernet
        _fernet_instance = Fernet(raw_key.encode())
        logger.info("HYGIE_ENCRYPTION_KEY loaded — sensitive settings encrypted at rest")
    except Exception as e:
        logger.warning(f"Invalid HYGIE_ENCRYPTION_KEY ({e}) — storing settings in plaintext")
    return _fernet_instance


def _encrypt_value(value: str) -> str:
    """Encrypt value if Fernet is available and value is non-empty."""
    f = _get_fernet()
    if not f or not value:
        return value
    return _ENC_PREFIX + f.encrypt(value.encode()).decode()


def _decrypt_value(value: str) -> str:
    """Decrypt value if it carries the enc: prefix; return as-is otherwise."""
    if not value or not value.startswith(_ENC_PREFIX):
        return value
    f = _get_fernet()
    if not f:
        logger.warning("Encrypted value found but HYGIE_ENCRYPTION_KEY is not set — cannot decrypt")
        return value
    try:
        return f.decrypt(value[len(_ENC_PREFIX):].encode()).decode()
    except Exception as e:
        logger.error(f"Failed to decrypt setting: {e}")
        return value


async def _migrate_encrypt_settings(db) -> None:
    """Encrypt any plaintext sensitive settings when the encryption key is available.

    Uses the DbConn abstraction so this works on both SQLite and MariaDB.
    """
    if not _get_fernet():
        return
    placeholders = ",".join("?" * len(SENSITIVE_KEYS))
    rows = await db.fetch_all(
        f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
        tuple(SENSITIVE_KEYS),
    )
    migrated = 0
    for row in rows:
        key, value = row["key"], row["value"]
        if value and not value.startswith(_ENC_PREFIX):
            await db.execute(
                "UPDATE settings SET value=? WHERE key=?",
                (_encrypt_value(value), key),
            )
            migrated += 1
    if migrated:
        await db.commit()
        logger.info(f"Encrypted {migrated} sensitive setting(s) in database")
