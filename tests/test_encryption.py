"""Unit tests for db/encryption.py — Fernet-at-rest for sensitive settings.

Previously untested: a bug here either breaks decryption of already-stored API
keys/webhooks in prod (Hygie becomes unusable until manually fixed) or silently
leaves secrets in plaintext. See CLAUDE.md piège 4 (SSRF) — this module carries
the same "must not silently misbehave" weight for secret handling.
"""
import pytest

import backend.db.encryption as enc
from backend.db.encryption import (
    _encrypt_value,
    _decrypt_value,
    _get_fernet,
    _migrate_encrypt_settings,
)


@pytest.fixture(autouse=True)
def _reset_fernet_cache():
    """The module caches the Fernet instance in globals after first use — reset
    around every test so tests that flip HYGIE_ENCRYPTION_KEY don't leak state."""
    saved_instance, saved_loaded = enc._fernet_instance, enc._fernet_loaded
    yield
    enc._fernet_instance, enc._fernet_loaded = saved_instance, saved_loaded


# ─── _encrypt_value / _decrypt_value round trip ───────────────────────────────

def test_encrypt_then_decrypt_round_trips():
    original = "sk-super-secret-api-key"
    encrypted = _encrypt_value(original)
    assert encrypted != original
    assert encrypted.startswith("enc:")
    assert _decrypt_value(encrypted) == original


def test_encrypt_empty_value_is_noop():
    assert _encrypt_value("") == ""


def test_decrypt_plaintext_without_prefix_returns_as_is():
    # Backward compat: settings written before HYGIE_ENCRYPTION_KEY was ever set.
    assert _decrypt_value("plain-old-value") == "plain-old-value"


def test_decrypt_empty_value_returns_as_is():
    assert _decrypt_value("") == ""


def test_decrypt_corrupted_encrypted_value_degrades_gracefully():
    # A truncated/corrupted enc: payload must not crash the caller — the
    # setting is unusable either way, but the app must keep booting.
    corrupted = "enc:not-a-real-fernet-token"
    assert _decrypt_value(corrupted) == corrupted


# ─── _get_fernet — key presence / validity ────────────────────────────────────

def test_get_fernet_returns_none_when_key_not_configured(monkeypatch):
    monkeypatch.delenv("HYGIE_ENCRYPTION_KEY", raising=False)
    enc._fernet_loaded = False
    enc._fernet_instance = None
    assert _get_fernet() is None


def test_get_fernet_returns_none_when_key_malformed(monkeypatch):
    monkeypatch.setenv("HYGIE_ENCRYPTION_KEY", "not-a-valid-fernet-key")
    enc._fernet_loaded = False
    enc._fernet_instance = None
    assert _get_fernet() is None


def test_get_fernet_caches_instance_across_calls(monkeypatch):
    monkeypatch.setenv("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")
    enc._fernet_loaded = False
    enc._fernet_instance = None
    first = _get_fernet()
    second = _get_fernet()
    assert first is not None
    assert first is second  # same cached instance, not re-parsed from env


def test_encrypt_value_is_noop_without_key(monkeypatch):
    monkeypatch.delenv("HYGIE_ENCRYPTION_KEY", raising=False)
    enc._fernet_loaded = False
    enc._fernet_instance = None
    assert _encrypt_value("some-secret") == "some-secret"


# ─── _migrate_encrypt_settings — one-time plaintext → enc: backfill ───────────

@pytest.fixture(autouse=True)
async def fresh_db(monkeypatch, tmp_path):
    """Own temp DB per test — mirrors tests/test_database.py's fresh_db fixture."""
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _db_ss
    import backend.db.schema as _db_schema
    import backend.db.engine as _db_engine

    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()
    yield db_path


async def test_migrate_encrypt_settings_encrypts_plaintext_sensitive_keys(monkeypatch):
    from backend.db.engine import get_db

    monkeypatch.setenv("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")
    enc._fernet_loaded = False
    enc._fernet_instance = None

    # Seed plaintext directly (bypassing set_setting, which would already encrypt),
    # simulating settings written before HYGIE_ENCRYPTION_KEY was configured.
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (`key`, value) VALUES (?, ?)",
            ("radarr_api_key", "plaintext-radarr-key"),
        )
        await db.execute(
            "INSERT OR REPLACE INTO settings (`key`, value) VALUES (?, ?)",
            ("ui_language", "fr"),  # non-sensitive — must be left untouched
        )
        await db.commit()

    async with get_db() as db:
        await _migrate_encrypt_settings(db)

    async with get_db() as db:
        rows = {
            r["key"]: r["value"]
            for r in await db.fetch_all("SELECT `key`, value FROM settings")
        }

    assert rows["radarr_api_key"].startswith("enc:")
    assert _decrypt_value(rows["radarr_api_key"]) == "plaintext-radarr-key"
    assert rows["ui_language"] == "fr"  # non-sensitive key never touched


async def test_migrate_encrypt_settings_leaves_already_encrypted_values_alone(monkeypatch):
    from backend.db.engine import get_db

    monkeypatch.setenv("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")
    enc._fernet_loaded = False
    enc._fernet_instance = None

    already_encrypted = _encrypt_value("already-safe")
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (`key`, value) VALUES (?, ?)",
            ("seerr_api_key", already_encrypted),
        )
        await db.commit()

    async with get_db() as db:
        await _migrate_encrypt_settings(db)

    async with get_db() as db:
        row = await db.fetch_one("SELECT value FROM settings WHERE `key`='seerr_api_key'")

    # Re-encrypting an already-encrypted value would produce a different
    # ciphertext each time (Fernet includes a random IV) — asserting equality
    # proves the migration skipped it rather than double-wrapping it.
    assert row["value"] == already_encrypted


async def test_migrate_encrypt_settings_is_noop_without_key(monkeypatch):
    from backend.db.engine import get_db

    monkeypatch.delenv("HYGIE_ENCRYPTION_KEY", raising=False)
    enc._fernet_loaded = False
    enc._fernet_instance = None

    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (`key`, value) VALUES (?, ?)",
            ("radarr_api_key", "plaintext-radarr-key"),
        )
        await db.commit()
        await _migrate_encrypt_settings(db)  # must return early, no key configured

    async with get_db() as db:
        row = await db.fetch_one("SELECT value FROM settings WHERE `key`='radarr_api_key'")
    assert row["value"] == "plaintext-radarr-key"
