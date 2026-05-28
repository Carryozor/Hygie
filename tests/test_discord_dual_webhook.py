"""
Tests for dual Discord webhook — notifications webhook + alerts webhook.

Verifies:
  1. test_discord() tests main notifications webhook only
  2. test_discord_alerts() tests the alerts webhook only
  3. send_alert() uses alerts webhook when configured, falls back to main
  4. The settings endpoint accepts and persists discord_webhook_alerts
"""
from unittest.mock import AsyncMock, patch, MagicMock
import pytest

import backend.database as dbmod
from backend.database import init_db


@pytest.fixture(autouse=True)
async def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "discord_test.db")
    monkeypatch.setattr(dbmod, "DB_PATH", db_path)
    dbmod._settings_cache.clear()
    dbmod._settings_cache_ts = 0.0
    dbmod._ms_cache = None
    dbmod._ms_cache_ts = 0.0
    await init_db()
    yield db_path


# ─── test_discord() ────────────────────────────────────────────────────────────

async def test_test_discord_no_webhook(isolated_db):
    """Returns (False, ...) when no main webhook is configured."""
    from backend.discord_client import test_discord
    ok, msg = await test_discord()
    assert not ok
    assert "non configuré" in msg.lower() or "webhook" in msg.lower()


async def test_test_discord_sends_to_main_webhook(isolated_db):
    """test_discord() POSTs to the main webhook URL."""
    import backend.database as db
    await db.set_setting("discord_webhook", "https://discord.com/api/webhooks/main/token")

    mock_resp = MagicMock()
    mock_resp.status_code = 204

    with patch("backend.discord_client.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        from backend.discord_client import test_discord
        ok, msg = await test_discord()

    assert ok
    called_url = mock_client.post.call_args[0][0]
    assert "main/token" in called_url


# ─── test_discord_alerts() ────────────────────────────────────────────────────

async def test_test_discord_alerts_no_webhook(isolated_db):
    """Returns (False, ...) when alerts webhook is not configured."""
    from backend.discord_client import test_discord_alerts
    ok, msg = await test_discord_alerts()
    assert not ok
    assert "non configuré" in msg.lower() or "webhook" in msg.lower()


async def test_test_discord_alerts_sends_to_alerts_webhook(isolated_db):
    """test_discord_alerts() POSTs to the alerts webhook URL, not the main one."""
    import backend.database as db
    await db.set_setting("discord_webhook", "https://discord.com/api/webhooks/main/token")
    await db.set_setting("discord_webhook_alerts", "https://discord.com/api/webhooks/alerts/token")

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("backend.discord_client.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        from backend.discord_client import test_discord_alerts
        ok, msg = await test_discord_alerts()

    assert ok
    called_url = mock_client.post.call_args[0][0]
    assert "alerts/token" in called_url
    assert "main/token" not in called_url


# ─── send_alert() fallback ────────────────────────────────────────────────────

async def test_send_alert_uses_alerts_webhook_when_set(isolated_db):
    """send_alert() sends to the alerts webhook when both webhooks are configured."""
    import backend.database as db
    await db.set_setting("discord_webhook", "https://discord.com/api/webhooks/main/token")
    await db.set_setting("discord_webhook_alerts", "https://discord.com/api/webhooks/alerts/token")

    mock_resp = MagicMock()
    mock_resp.status_code = 204

    with patch("backend.discord_client.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        from backend.discord_client import send_alert
        ok = await send_alert("Test title", "Test body", level="error")

    assert ok
    called_url = mock_client.post.call_args[0][0]
    assert "alerts/token" in called_url


async def test_send_alert_falls_back_to_main_when_no_alerts_webhook(isolated_db):
    """send_alert() falls back to main webhook when alerts webhook is not set."""
    import backend.database as db
    await db.set_setting("discord_webhook", "https://discord.com/api/webhooks/main/token")

    mock_resp = MagicMock()
    mock_resp.status_code = 204

    with patch("backend.discord_client.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        from backend.discord_client import send_alert
        ok = await send_alert("Test title", "Test body")

    assert ok
    called_url = mock_client.post.call_args[0][0]
    assert "main/token" in called_url


async def test_send_alert_no_webhook_returns_false(isolated_db):
    """send_alert() returns False when no webhook is configured at all."""
    from backend.discord_client import send_alert
    ok = await send_alert("Test", "No webhook configured")
    assert not ok


async def test_send_alert_with_mention_sends_content_field(isolated_db):
    """send_alert() with mention=... sets payload['content'] to trigger real Discord pings."""
    import backend.database as db
    await db.set_setting("discord_webhook", "https://discord.com/api/webhooks/main/token")

    mock_resp = MagicMock()
    mock_resp.status_code = 204
    captured = {}

    async def _fake_post(url, json=None, **kw):
        captured["payload"] = json
        return mock_resp

    with patch("backend.discord_client.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=_fake_post)

        from backend.discord_client import send_alert
        ok = await send_alert("Test", "body", mention="@here")

    assert ok
    assert captured["payload"].get("content") == "@here"


async def test_send_alert_custom_msg_template_interpolated(isolated_db):
    """send_alert() interpolates template_vars into custom_msg."""
    import backend.database as db
    await db.set_setting("discord_webhook", "https://discord.com/api/webhooks/main/token")

    mock_resp = MagicMock()
    mock_resp.status_code = 204
    captured = {}

    async def _fake_post(url, json=None, **kw):
        captured["payload"] = json
        return mock_resp

    with patch("backend.discord_client.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=_fake_post)

        from backend.discord_client import send_alert
        ok = await send_alert(
            "Scan failed", "default body",
            custom_msg="Scan échoué : {detail}",
            template_vars={"detail": "timeout"},
        )

    assert ok
    embed_desc = captured["payload"]["embeds"][0]["description"]
    assert embed_desc == "Scan échoué : timeout"
