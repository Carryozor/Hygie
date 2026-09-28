"""Unit tests for db/encryption.py — Fernet-at-rest for sensitive settings.

Previously untested: a bug here either breaks decryption of already-stored API
keys/webhooks in prod (Hygie becomes unusable until manually fixed) or silently
leaves secrets in plaintext. See CLAUDE.md piège 4 (SSRF) — this module carries
the same "must not silently misbehave" weight for secret handling.
"""
import os

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

@pytest.fixture
def isolated_key_dir(monkeypatch, tmp_path):
    """Point the encryption-key-file mechanism at a throwaway directory so
    these tests never touch the real (gitignored) .encryption_key next to
    the repo's .secret file, and so each test starts with no key file."""
    import backend.db.utils as _db_utils
    monkeypatch.setattr(_db_utils, "DB_PATH", str(tmp_path / "hygie.db"))
    return tmp_path


def test_mariadb_dialect_without_env_var_and_no_existing_file_stays_plaintext(monkeypatch, isolated_key_dir):
    """Auto-generating a key is only safe when it shares the DB's own
    persistence. With SQLite the key file sits next to hygie.db, on the same
    volume. With MariaDB the database lives in a separate server — if the
    key file's directory (e.g. /app/data) is not itself a persistent
    volume, a freshly generated key is lost on the next container recreate
    and every value encrypted with the old key becomes permanently
    unreadable, which is worse than the old plaintext-with-a-WARN behavior.
    Must stay in plaintext (logging a clear WARNING) instead of generating."""
    import backend.db.engine as _db_engine
    monkeypatch.setattr(_db_engine, "DIALECT", "mariadb")
    monkeypatch.delenv("HYGIE_ENCRYPTION_KEY", raising=False)
    enc._fernet_loaded = False
    enc._fernet_instance = None

    fernet = _get_fernet()

    assert fernet is None
    key_file = isolated_key_dir / ".encryption_key"
    assert not key_file.exists()


def test_mariadb_dialect_loads_an_existing_key_file_if_present(monkeypatch, isolated_key_dir):
    """MariaDB must not regress someone who already has a key file (e.g.
    migrated from SQLite, or placed there manually) — only NEW generation
    is refused, not use of an existing file."""
    from cryptography.fernet import Fernet
    import backend.db.engine as _db_engine

    existing_key = Fernet.generate_key()
    key_file = isolated_key_dir / ".encryption_key"
    key_file.write_bytes(existing_key)

    monkeypatch.setattr(_db_engine, "DIALECT", "mariadb")
    monkeypatch.delenv("HYGIE_ENCRYPTION_KEY", raising=False)
    enc._fernet_loaded = False
    enc._fernet_instance = None

    fernet = _get_fernet()

    assert fernet is not None
    assert fernet.decrypt(Fernet(existing_key).encrypt(b"x")) == b"x"


def test_in_memory_db_path_never_writes_a_key_file(monkeypatch):
    """DB_PATH == ':memory:' (tests, and any ephemeral SQLite setup) must
    never generate a key file — dirname('') resolves to the current working
    directory, which would litter the repo/CWD with a real secret."""
    import backend.db.utils as _db_utils
    monkeypatch.setattr(_db_utils, "DB_PATH", ":memory:")
    monkeypatch.delenv("HYGIE_ENCRYPTION_KEY", raising=False)
    enc._fernet_loaded = False
    enc._fernet_instance = None

    fernet = _get_fernet()

    assert fernet is None
    assert not os.path.exists(".encryption_key")


def test_get_fernet_without_env_var_falls_back_to_a_persisted_key_file(monkeypatch, isolated_key_dir):
    """Without HYGIE_ENCRYPTION_KEY, secrets must NOT silently stay in
    plaintext forever: a key is generated and persisted (mirrors
    auth._load_or_create_secret()), so _get_fernet() returns a usable
    Fernet instance instead of None."""
    monkeypatch.delenv("HYGIE_ENCRYPTION_KEY", raising=False)
    enc._fernet_loaded = False
    enc._fernet_instance = None

    fernet = _get_fernet()

    assert fernet is not None
    key_file = isolated_key_dir / ".encryption_key"
    assert key_file.exists()


def test_encryption_key_file_is_created_with_mode_600(monkeypatch, isolated_key_dir):
    import stat
    monkeypatch.delenv("HYGIE_ENCRYPTION_KEY", raising=False)
    enc._fernet_loaded = False
    enc._fernet_instance = None

    _get_fernet()

    key_file = isolated_key_dir / ".encryption_key"
    mode = stat.S_IMODE(key_file.stat().st_mode)
    assert mode == 0o600


def test_encryption_key_file_is_reused_across_calls_restarts(monkeypatch, isolated_key_dir):
    """A second 'process' (simulated by resetting the cached Fernet
    instance, the only in-memory state) must load the SAME key from disk
    rather than generating a new one — otherwise every value encrypted by
    the first process becomes undecryptable."""
    monkeypatch.delenv("HYGIE_ENCRYPTION_KEY", raising=False)
    enc._fernet_loaded = False
    enc._fernet_instance = None

    first = _get_fernet()
    encrypted = first.encrypt(b"secret-value")

    # Simulate a restart: forget the cached instance, but the file on disk survives.
    enc._fernet_loaded = False
    enc._fernet_instance = None
    second = _get_fernet()

    assert second.decrypt(encrypted) == b"secret-value"


def test_invalid_key_file_is_not_overwritten_and_falls_back_to_plaintext(monkeypatch, isolated_key_dir):
    """An existing-but-corrupt key file must never be silently replaced —
    that would orphan any values already encrypted with the real key. Must
    degrade to plaintext mode (None) instead of crashing."""
    monkeypatch.delenv("HYGIE_ENCRYPTION_KEY", raising=False)
    enc._fernet_loaded = False
    enc._fernet_instance = None

    key_file = isolated_key_dir / ".encryption_key"
    key_file.write_bytes(b"not-a-valid-fernet-key")
    original_content = key_file.read_bytes()

    fernet = _get_fernet()

    assert fernet is None
    assert key_file.read_bytes() == original_content  # untouched, not regenerated


def test_env_var_wins_over_persisted_key_file(monkeypatch, isolated_key_dir):
    """HYGIE_ENCRYPTION_KEY, when set, must always be used even if a key
    file already exists on disk."""
    from cryptography.fernet import Fernet

    file_key = Fernet.generate_key()
    key_file = isolated_key_dir / ".encryption_key"
    key_file.write_bytes(file_key)

    env_key = Fernet.generate_key()
    monkeypatch.setenv("HYGIE_ENCRYPTION_KEY", env_key.decode())
    enc._fernet_loaded = False
    enc._fernet_instance = None

    fernet = _get_fernet()
    encrypted = fernet.encrypt(b"x")

    # Decryptable with the env key, NOT with the on-disk file key.
    assert Fernet(env_key).decrypt(encrypted) == b"x"
    with pytest.raises(Exception):
        Fernet(file_key).decrypt(encrypted)


def test_legacy_plaintext_value_remains_readable_with_file_backed_key(monkeypatch, isolated_key_dir):
    """A value written before any key existed (plain, no enc: prefix) must
    still decrypt (pass through) once a file-backed key is generated."""
    monkeypatch.delenv("HYGIE_ENCRYPTION_KEY", raising=False)
    enc._fernet_loaded = False
    enc._fernet_instance = None

    assert _decrypt_value("plain-legacy-value") == "plain-legacy-value"

    # And a fresh write now gets encrypted under the generated key.
    encrypted = _encrypt_value("new-value")
    assert encrypted.startswith("enc:")
    assert _decrypt_value(encrypted) == "new-value"


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


def test_encrypt_value_falls_back_to_plaintext_when_key_file_cannot_be_persisted(monkeypatch, isolated_key_dir):
    """Genuine 'no key available' case now requires key-file persistence
    itself to fail (env var absent no longer means plaintext by default —
    that's exactly the bug being fixed here). Simulated by making the write
    raise — chmod-based read-only dirs don't block root, and tests run as
    root in this environment."""
    monkeypatch.delenv("HYGIE_ENCRYPTION_KEY", raising=False)
    enc._fernet_loaded = False
    enc._fernet_instance = None
    monkeypatch.setattr(enc.os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only filesystem")))

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


async def test_migrate_encrypt_settings_uses_generated_key_when_env_var_absent(monkeypatch):
    """Without HYGIE_ENCRYPTION_KEY, a key is now generated/persisted
    automatically (this fix's whole point), so the migration must actually
    encrypt plaintext sensitive settings rather than silently no-op."""
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
        await _migrate_encrypt_settings(db)

    async with get_db() as db:
        row = await db.fetch_one("SELECT value FROM settings WHERE `key`='radarr_api_key'")
    assert row["value"].startswith("enc:")
    assert _decrypt_value(row["value"]) == "plaintext-radarr-key"


async def test_migrate_encrypt_settings_is_noop_when_truly_no_key_available(monkeypatch):
    """Genuine no-key case: env var absent AND the key file cannot be
    persisted — must still no-op, not crash. Simulated by making the key
    directory creation raise (chmod-based read-only dirs don't block root,
    and tests run as root in this environment)."""
    from backend.db.engine import get_db

    monkeypatch.delenv("HYGIE_ENCRYPTION_KEY", raising=False)
    enc._fernet_loaded = False
    enc._fernet_instance = None
    monkeypatch.setattr(enc.os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only filesystem")))

    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (`key`, value) VALUES (?, ?)",
            ("radarr_api_key", "plaintext-radarr-key"),
        )
        await db.commit()
        await _migrate_encrypt_settings(db)

    async with get_db() as db:
        row = await db.fetch_one("SELECT value FROM settings WHERE `key`='radarr_api_key'")
    assert row["value"] == "plaintext-radarr-key"
