"""Fixes from the independent security review of the 4.4.3 hardening diff."""
from unittest.mock import patch

import pytest

from tests.test_security_hardening_ssrf import settings_client  # noqa: F401  (fixture)


# ─── Settings save must not re-resolve URLs the user did not change ──────────
@pytest.mark.asyncio
async def test_saving_unrelated_setting_does_not_revalidate_unchanged_urls(settings_client):
    from backend.db.settings_store import set_setting
    # A stored service URL whose host does not resolve right now (service down).
    await set_setting("seerr_url", "http://service-down.invalid:5055")
    r = await settings_client.post("/api/settings", json={
        "seerr_url": "http://service-down.invalid:5055",   # unchanged, echoed back by the UI
        "discord_notif_thresholds": "",
    })
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_changing_a_url_to_an_unresolvable_host_is_still_refused(settings_client):
    from backend.db.settings_store import set_setting
    await set_setting("seerr_url", "http://192.168.1.10:5055")
    r = await settings_client.post("/api/settings", json={"seerr_url": "http://other-down.invalid:5055"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_unchanged_webhook_is_not_revalidated_but_a_new_internal_one_is(settings_client):
    from backend.db.settings_store import set_setting
    await set_setting("discord_webhook", "https://chat-down.invalid/api/webhooks/1/x")
    ok = await settings_client.post("/api/settings", json={
        "discord_webhook": "https://chat-down.invalid/api/webhooks/1/x"})
    assert ok.status_code == 200
    bad = await settings_client.post("/api/settings", json={"discord_webhook": "http://127.0.0.1/hook"})
    assert bad.status_code == 422


# ─── Proxy rate-limit: only meaningful when real client IPs are visible ──────
class _Req:
    def __init__(self):
        self.query_params = {}
        self.headers = {}
        self.client = type("C", (), {"host": "172.27.0.9"})()


def test_proxy_rate_limit_is_inactive_without_trust_proxy():
    import backend.proxy as proxy_mod
    proxy_mod._proxy_hits.clear()
    with patch.object(proxy_mod, "_TRUST_PROXY", False, create=True), \
         patch("backend.auth._TRUST_PROXY", False):
        assert not any(proxy_mod._proxy_rate_limited(_Req()) for _ in range(proxy_mod._PROXY_RATE_MAX + 50))


def test_proxy_rate_limit_applies_when_trust_proxy_is_active():
    import backend.proxy as proxy_mod
    proxy_mod._proxy_hits.clear()
    with patch.object(proxy_mod, "_TRUST_PROXY", True, create=True), \
         patch("backend.auth._TRUST_PROXY", True):
        results = [proxy_mod._proxy_rate_limited(_Req()) for _ in range(proxy_mod._PROXY_RATE_MAX + 5)]
    assert results[-1] is True


@pytest.mark.asyncio
async def test_proxy_429_is_not_cacheable():
    import backend.proxy as proxy_mod
    proxy_mod._proxy_hits.clear()
    with patch.object(proxy_mod, "_proxy_rate_limited", return_value=True):
        resp = await proxy_mod.proxy_image(_Req())
    assert resp.status_code == 429
    assert resp.headers.get("cache-control") == "no-store"


# ─── Login rate-limit key: accent/case/width variants share one budget ───────
def test_login_rate_limit_key_folds_accents_case_and_width():
    from backend.routers.auth import _rl_key
    base = _rl_key("login", "1.2.3.4", "admin")
    for variant in ("Admin", " admin ", "admín", "ａdmin", "ADMIN"):
        assert _rl_key("login", "1.2.3.4", variant) == base, variant
    assert _rl_key("login", "1.2.3.4", "other") != base


# ─── MariaDB boot encrypts existing plaintext sensitive settings ─────────────
@pytest.mark.asyncio
async def test_mariadb_init_encrypts_existing_plaintext_sensitive_settings(monkeypatch, tmp_path):
    import backend.db.encryption as enc
    import backend.db.engine as eng
    import backend.db.schema as schema
    import backend.db.settings_store as ss
    import backend.db.utils as utils

    db_path = str(tmp_path / "m.db")
    for mod, attr in ((utils, "DB_PATH"), (ss, "DB_PATH"), (schema, "DB_PATH"), (eng, "SQLITE_PATH")):
        monkeypatch.setattr(mod, attr, db_path)
    ss._settings_cache.clear()
    await schema.init_db()
    monkeypatch.setenv("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")
    enc._fernet_loaded = False
    enc._fernet_instance = None

    async with eng.get_db() as db:
        await db.execute("DELETE FROM settings WHERE `key`='public_dashboard_password'")
        await db.execute("INSERT INTO settings (`key`, value) VALUES (?, ?)",
                         ("public_dashboard_password", "plain-secret"))
        await db.commit()

    # The MariaDB init path is exercised on the SQLite test engine: only the
    # trailing "encrypt existing plaintext" step is under test here.
    await schema._encrypt_existing_sensitive_settings()

    async with eng.get_db() as db:
        row = await db.fetch_one("SELECT value FROM settings WHERE `key`='public_dashboard_password'")
    assert row["value"].startswith("enc:")
    ss._settings_cache.clear()
    assert await ss.get_setting("public_dashboard_password") == "plain-secret"
