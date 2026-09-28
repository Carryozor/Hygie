"""Fernet encryption helpers for sensitive settings."""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_ENC_PREFIX = "enc:"

# Same directory as auth.py's SECRET_FILE (JWT signing secret) — computed
# lazily from the current db.utils.DB_PATH rather than cached once at import
# (auth.SECRET_FILE is a module-level constant baked in at first import,
# which is fine for that module's needs but would make this file impossible
# to point at a per-test tmp_path; reading DB_PATH at call time gives the
# same "next to the data dir" placement without that limitation).
_ENCRYPTION_KEY_FILENAME = ".encryption_key"


def _encryption_key_file() -> str:
    from .utils import DB_PATH
    return os.path.join(os.path.dirname(DB_PATH), _ENCRYPTION_KEY_FILENAME)

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


def _load_or_create_encryption_key() -> Optional[bytes]:
    """Resolve the Fernet key: HYGIE_ENCRYPTION_KEY env var if set (always
    wins), else load/generate one persisted next to auth.py's JWT .secret
    file — mirroring auth._load_or_create_secret() so secrets-at-rest don't
    silently degrade to plaintext just because the env var was never set.

    Never generates a new key over an existing (even if invalid) key file:
    doing so would orphan every value already encrypted with the old key.
    An invalid file logs an ERROR and falls back to plaintext mode instead.
    """
    env_key = os.environ.get("HYGIE_ENCRYPTION_KEY", "").strip()
    if env_key:
        return env_key.encode()

    key_file = _encryption_key_file()

    if os.path.exists(key_file):
        try:
            with open(key_file, "rb") as f:
                raw = f.read().strip()
            from cryptography.fernet import Fernet
            Fernet(raw)  # validate — raises if malformed
            return raw
        except Exception as e:
            logger.error(
                f"Encryption key file at {key_file} is invalid ({e}) — "
                "falling back to plaintext storage rather than generating a "
                "new key over it, which would make every already-encrypted "
                "setting permanently undecryptable"
            )
            return None

    try:
        from cryptography.fernet import Fernet
        new_key = Fernet.generate_key()
        key_dir = os.path.dirname(key_file)
        if key_dir:
            os.makedirs(key_dir, exist_ok=True)
        with open(key_file, "wb") as f:
            f.write(new_key)
        os.chmod(key_file, 0o600)
        logger.warning(
            f"HYGIE_ENCRYPTION_KEY is not set — generated and persisted an "
            f"encryption key at {key_file} (mode 600). Recommended: set "
            f"HYGIE_ENCRYPTION_KEY explicitly in your environment and back "
            f"up this file — losing it makes every encrypted setting "
            f"permanently unrecoverable."
        )
        return new_key
    except Exception as e:
        logger.warning(f"Could not persist encryption key ({e}) — storing settings in plaintext")
        return None


def _get_fernet():
    """Return a Fernet instance for the resolved encryption key, or None
    when no usable key is available (settings then stay in plaintext)."""
    global _fernet_instance, _fernet_loaded
    if _fernet_loaded:
        return _fernet_instance
    _fernet_loaded = True
    raw_key = _load_or_create_encryption_key()
    if not raw_key:
        return None
    try:
        from cryptography.fernet import Fernet
        _fernet_instance = Fernet(raw_key)
        logger.info("Encryption key loaded — sensitive settings encrypted at rest")
    except Exception as e:
        logger.warning(f"Invalid encryption key ({e}) — storing settings in plaintext")
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
        f"SELECT `key`, value FROM settings WHERE `key` IN ({placeholders})",  # nosec B608 - placeholders count from fixed SENSITIVE_KEYS, values bound
        tuple(SENSITIVE_KEYS),
    )
    migrated = 0
    for row in rows:
        key, value = row["key"], row["value"]
        if value and not value.startswith(_ENC_PREFIX):
            await db.execute(
                "UPDATE settings SET value=? WHERE `key`=?",
                (_encrypt_value(value), key),
            )
            migrated += 1
    if migrated:
        await db.commit()
        logger.info(f"Encrypted {migrated} sensitive setting(s) in database")
