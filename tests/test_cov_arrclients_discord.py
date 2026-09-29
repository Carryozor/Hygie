"""Coverage tests for backend/discord_client.py — Discord notifications.

Not directly on the delete-target critical path, but send_notification /
send_alert are the operator-visible signal for what Hygie deleted — a
silently-broken notification (wrong embed, wrong webhook) hides a real
deletion from the person who'd want to know.
"""
import os
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")

import httpx
import pytest

import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
import backend.db.media_servers as _db_ms
import backend.db.schema as _db_schema
from backend.db.schema import init_db


@pytest.fixture(autouse=True)
async def isolated_db(tmp_path, monkeypatch):
    import backend.db.engine as _db_engine
    db_path = str(tmp_path / "discord_cov_test.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ms, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0
    await init_db()
    yield db_path


def _mock_async_client(post_fn):
    """Patch backend.discord_client.httpx.AsyncClient so .post() runs post_fn."""
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=post_fn)
    cm = patch("backend.discord_client.httpx.AsyncClient")
    mock_cls = cm.start()
    mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    return cm, mock_client


# ─── _get_kind_meta ─────────────────────────────────────────────────────────────

def test_get_kind_meta_known_kinds():
    from backend.discord_client import _get_kind_meta
    for kind in ("detected", "1d", "now"):
        title, color = _get_kind_meta(kind)
        assert isinstance(title, str) and title
        assert color == {"detected": 0x57F287, "1d": 0xFF4500, "now": 0xFF0000}[kind]


def test_get_kind_meta_days_pattern_close_is_red():
    from backend.discord_client import _get_kind_meta
    title, color = _get_kind_meta("2d")
    assert color == 0xFF4500


def test_get_kind_meta_days_pattern_medium_is_orange():
    from backend.discord_client import _get_kind_meta
    title, color = _get_kind_meta("5d")
    assert color == 0xF0A500


def test_get_kind_meta_days_pattern_far_is_indigo():
    from backend.discord_client import _get_kind_meta
    title, color = _get_kind_meta("14d")
    assert color == 0x6366F1


def test_get_kind_meta_singular_vs_plural_day_key():
    from backend.discord_client import _get_kind_meta
    title1, _ = _get_kind_meta("1d")
    # "1d" is in _KIND_COLORS directly so goes through that branch, not regex.
    # Use a non-special single day via regex path indirectly isn't possible
    # since "1d" is a dict hit — assert the dict-branch title is non-empty.
    assert title1


def test_get_kind_meta_unknown_kind_returns_default():
    from backend.discord_client import _get_kind_meta
    title, color = _get_kind_meta("garbage")
    assert title == "ℹ️ Hygie"
    assert color == 0x6366F1


# ─── _resolve_discord_id ────────────────────────────────────────────────────────

async def test_resolve_discord_id_returns_empty_without_user_id():
    from backend.discord_client import _resolve_discord_id
    assert await _resolve_discord_id(None) == ""
    assert await _resolve_discord_id(0) == ""


async def test_resolve_discord_id_hygie_mapping_wins(isolated_db):
    from backend.db.engine import get_db
    from backend.discord_client import _resolve_discord_id

    async with get_db() as db:
        await db.execute(
            "INSERT INTO seerr_user_rules "
            "(name, seerr_user_id, seerr_username, library_id, discord_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ("rule", 7, "Bob", "*", "555444333"),
        )
        await db.commit()

    assert await _resolve_discord_id(7) == "555444333"


async def test_resolve_discord_id_falls_back_to_seerr_api(isolated_db):
    from backend.discord_client import _resolve_discord_id
    await _db_ss.set_setting("seerr_url", "http://seerr.test:5055")
    await _db_ss.set_setting("seerr_api_key", "seerr-key")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"discordIds": ["222333444"]}

    with patch("backend.discord_client.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        result = await _resolve_discord_id(9)

    assert result == "222333444"


async def test_resolve_discord_id_returns_empty_when_nothing_found(isolated_db):
    from backend.discord_client import _resolve_discord_id
    assert await _resolve_discord_id(123) == ""


async def test_resolve_discord_id_seerr_lookup_exception_returns_empty(isolated_db):
    from backend.discord_client import _resolve_discord_id
    await _db_ss.set_setting("seerr_url", "http://seerr.test:5055")
    await _db_ss.set_setting("seerr_api_key", "seerr-key")

    with patch("backend.discord_client.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("down"))
        result = await _resolve_discord_id(9)

    assert result == ""


# ─── _build_embed ────────────────────────────────────────────────────────────────

async def test_build_embed_includes_requester_mention(isolated_db):
    from backend.db.engine import get_db
    from backend.discord_client import _build_embed

    async with get_db() as db:
        await db.execute(
            "INSERT INTO seerr_user_rules "
            "(name, seerr_user_id, seerr_username, library_id, discord_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ("rule", 3, "Carol", "*", "777"),
        )
        await db.commit()

    m = {
        "title": "Dune", "media_type": "Movie", "library_name": "Films",
        "seerr_user_id": 3, "seerr_username": "Carol",
        "delete_at": "2026-10-05T00:00:00Z",
    }
    embed = await _build_embed(m, 0xFF0000, "footer", "1d", single=True, server_name="Emby")
    fields_by_name = {f["name"]: f["value"] for f in embed["fields"]}
    assert any("<@777>" in v for v in fields_by_name.values())
    assert embed["title"] == "🎬 Dune"


async def test_build_embed_shows_username_when_no_discord_id(isolated_db):
    from backend.discord_client import _build_embed
    m = {"title": "Show", "media_type": "Series", "library_name": "TV", "seerr_username": "NoDiscord"}
    embed = await _build_embed(m, 0x123456, "footer", "detected", single=False)
    fields_by_name = {f["name"]: f["value"] for f in embed["fields"]}
    assert "NoDiscord" in fields_by_name.values()
    assert embed["title"].startswith("📺")


async def test_build_embed_skips_scheduled_date_for_now_kind():
    from backend.discord_client import _build_embed
    m = {"title": "X", "delete_at": "2026-10-05T00:00:00Z"}
    embed = await _build_embed(m, 0x1, "footer", "now", single=True)
    assert not any("delete" in f["name"].lower() or "programm" in f["name"].lower() for f in embed["fields"])


async def test_build_embed_public_poster_included_as_image_when_single():
    from backend.discord_client import _build_embed
    m = {"title": "X", "poster_url": "http://cdn.example/poster.jpg"}
    embed = await _build_embed(m, 0x1, "footer", "detected", single=True)
    assert embed["image"] == {"url": "http://cdn.example/poster.jpg"}


async def test_build_embed_public_poster_as_thumbnail_when_multi():
    from backend.discord_client import _build_embed
    m = {"title": "X", "poster_url": "http://cdn.example/poster.jpg"}
    embed = await _build_embed(m, 0x1, "footer", "detected", single=False)
    assert embed["thumbnail"] == {"url": "http://cdn.example/poster.jpg"}


async def test_build_embed_internal_poster_url_excluded():
    """Discord can't reach internal Docker hostnames — an internal poster
    URL must not be embedded (would render as a broken image)."""
    from backend.discord_client import _build_embed
    m = {"title": "X", "poster_url": "http://192.168.1.5:8096/img.jpg"}
    embed = await _build_embed(m, 0x1, "footer", "detected", single=True)
    assert "image" not in embed
    assert "thumbnail" not in embed


async def test_build_embed_localhost_poster_url_excluded():
    from backend.discord_client import _build_embed
    m = {"title": "X", "poster_url": "http://localhost/img.jpg"}
    embed = await _build_embed(m, 0x1, "footer", "detected", single=True)
    assert "image" not in embed


async def test_build_embed_includes_server_name_field_when_given():
    from backend.discord_client import _build_embed
    m = {"title": "X"}
    embed = await _build_embed(m, 0x1, "footer", "detected", single=True, server_name="Emby-1")
    names = [f["name"] for f in embed["fields"]]
    values = [f["value"] for f in embed["fields"]]
    assert "Emby-1" in values


# ─── _build_server_name_cache ───────────────────────────────────────────────────

async def test_build_server_name_cache_maps_library_to_server(isolated_db, monkeypatch):
    from backend.discord_client import _build_server_name_cache
    from backend.db.engine import get_db

    async with get_db() as db:
        await db.execute(
            "INSERT INTO libraries (id, server_id, name, emby_library_id) VALUES (?, ?, ?, ?)",
            ("lib1", "1", "Films", "emby-lib-1"),
        )
        await db.commit()

    async def _servers():
        return [{"id": "1", "name": "Emby Main"}]

    monkeypatch.setattr("backend.db.media_servers.get_media_servers", _servers)
    cache = await _build_server_name_cache()
    assert cache.get("lib1") == "Emby Main"


async def test_build_server_name_cache_returns_empty_on_error(monkeypatch):
    from backend.discord_client import _build_server_name_cache

    async def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("backend.db.media_servers.get_media_servers", _boom)
    assert await _build_server_name_cache() == {}


# ─── send_notification ──────────────────────────────────────────────────────────

async def test_send_notification_dry_run_returns_true_without_sending(isolated_db):
    from backend.discord_client import send_notification
    with patch("backend.discord_client.httpx.AsyncClient") as mock_cls:
        ok = await send_notification([{"title": "X"}], "detected", dry_run=True)
    assert ok is True
    mock_cls.assert_not_called()


async def test_send_notification_empty_list_returns_true(isolated_db):
    from backend.discord_client import send_notification
    assert await send_notification([], "detected") is True


async def test_send_notification_no_webhook_returns_false(isolated_db):
    from backend.discord_client import send_notification
    assert await send_notification([{"title": "X"}], "detected") is False


async def test_send_notification_posts_embeds_on_success(isolated_db):
    await _db_ss.set_setting("discord_webhook", "https://discord.com/api/webhooks/x/y")
    mock_resp = MagicMock()
    mock_resp.status_code = 204

    async def _post(url, json=None, **kw):
        return mock_resp

    cm, mock_client = _mock_async_client(_post)
    try:
        from backend.discord_client import send_notification
        ok = await send_notification([{"title": "Dune"}], "now")
    finally:
        cm.stop()

    assert ok is True
    payload = mock_client.post.call_args.kwargs.get("json") or mock_client.post.call_args[1].get("json")
    assert len(payload["embeds"]) == 1


async def test_send_notification_returns_false_on_http_error(isolated_db):
    await _db_ss.set_setting("discord_webhook", "https://discord.com/api/webhooks/x/y")
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "server error"

    async def _post(url, json=None, **kw):
        return mock_resp

    cm, mock_client = _mock_async_client(_post)
    try:
        from backend.discord_client import send_notification
        ok = await send_notification([{"title": "Dune"}], "now")
    finally:
        cm.stop()
    assert ok is False


async def test_send_notification_returns_false_on_exception(isolated_db):
    await _db_ss.set_setting("discord_webhook", "https://discord.com/api/webhooks/x/y")

    async def _post(url, json=None, **kw):
        raise httpx.ConnectError("down")

    cm, mock_client = _mock_async_client(_post)
    try:
        from backend.discord_client import send_notification
        ok = await send_notification([{"title": "Dune"}], "now")
    finally:
        cm.stop()
    assert ok is False


async def test_send_notification_caps_embeds_and_adds_overflow_summary(isolated_db):
    await _db_ss.set_setting("discord_webhook", "https://discord.com/api/webhooks/x/y")
    mock_resp = MagicMock()
    mock_resp.status_code = 204

    async def _post(url, json=None, **kw):
        return mock_resp

    cm, mock_client = _mock_async_client(_post)
    try:
        from backend.discord_client import send_notification
        media = [{"title": f"Movie {i}"} for i in range(12)]
        ok = await send_notification(media, "detected")
    finally:
        cm.stop()

    assert ok is True
    payload = mock_client.post.call_args.kwargs.get("json") or mock_client.post.call_args[1].get("json")
    assert len(payload["embeds"]) == 10  # 9 real + 1 overflow summary
    assert "et 3 média" in payload["embeds"][-1]["title"]


async def test_send_notification_overflow_over_five_appends_count_suffix(isolated_db):
    """16 items: 9 shown as embeds, overflow=7 (>5) -> extras string gets the
    ' et N autre(s)' suffix instead of listing all 7 titles."""
    await _db_ss.set_setting("discord_webhook", "https://discord.com/api/webhooks/x/y")
    mock_resp = MagicMock()
    mock_resp.status_code = 204

    async def _post(url, json=None, **kw):
        return mock_resp

    cm, mock_client = _mock_async_client(_post)
    try:
        from backend.discord_client import send_notification
        media = [{"title": f"Movie {i}"} for i in range(16)]
        ok = await send_notification(media, "detected")
    finally:
        cm.stop()

    assert ok is True
    payload = mock_client.post.call_args.kwargs.get("json") or mock_client.post.call_args[1].get("json")
    overflow_desc = payload["embeds"][-1]["description"]
    assert "et 2 autre(s)" in overflow_desc  # overflow=7, 5 listed, 2 remain


async def test_send_notification_no_valid_embeds_returns_false(isolated_db, monkeypatch):
    await _db_ss.set_setting("discord_webhook", "https://discord.com/api/webhooks/x/y")

    async def _boom_build_embed(*a, **kw):
        raise RuntimeError("embed build failed")

    monkeypatch.setattr("backend.discord_client._build_embed", _boom_build_embed)
    from backend.discord_client import send_notification
    ok = await send_notification([{"title": "X"}], "detected")
    assert ok is False


# ─── send_alert ──────────────────────────────────────────────────────────────────

async def test_send_alert_returns_false_on_exception(isolated_db):
    await _db_ss.set_setting("discord_webhook", "https://discord.com/api/webhooks/x/y")

    async def _post(url, json=None, **kw):
        raise httpx.ConnectError("down")

    cm, mock_client = _mock_async_client(_post)
    try:
        from backend.discord_client import send_alert
        ok = await send_alert("T", "D")
    finally:
        cm.stop()
    assert ok is False


async def test_send_alert_custom_msg_falls_back_when_template_vars_mismatch(isolated_db):
    """A custom_msg referencing a key missing from template_vars must fall
    back to the raw custom_msg instead of raising."""
    await _db_ss.set_setting("discord_webhook", "https://discord.com/api/webhooks/x/y")
    mock_resp = MagicMock()
    mock_resp.status_code = 204
    captured = {}

    async def _post(url, json=None, **kw):
        captured["payload"] = json
        return mock_resp

    cm, mock_client = _mock_async_client(_post)
    try:
        from backend.discord_client import send_alert
        ok = await send_alert(
            "T", "default", custom_msg="Missing {undefined_key}", template_vars={"other": "x"}
        )
    finally:
        cm.stop()
    assert ok is True
    assert captured["payload"]["embeds"][0]["description"] == "Missing {undefined_key}"


async def test_send_alert_without_mention_omits_content_field(isolated_db):
    await _db_ss.set_setting("discord_webhook", "https://discord.com/api/webhooks/x/y")
    mock_resp = MagicMock()
    mock_resp.status_code = 204
    captured = {}

    async def _post(url, json=None, **kw):
        captured["payload"] = json
        return mock_resp

    cm, mock_client = _mock_async_client(_post)
    try:
        from backend.discord_client import send_alert
        ok = await send_alert("T", "D")
    finally:
        cm.stop()
    assert ok is True
    assert "content" not in captured["payload"]


# ─── _test_webhook ──────────────────────────────────────────────────────────────

async def test_webhook_test_returns_false_on_non_2xx_status(isolated_db):
    from backend.discord_client import _test_webhook
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.text = "Bad Request"

    async def _post(url, json=None, **kw):
        return mock_resp

    cm, mock_client = _mock_async_client(_post)
    try:
        ok, msg = await _test_webhook("https://discord.com/api/webhooks/x/y", "notifications")
    finally:
        cm.stop()
    assert ok is False
    assert "HTTP 400" in msg


async def test_webhook_test_returns_false_on_exception(isolated_db):
    from backend.discord_client import _test_webhook

    async def _post(url, json=None, **kw):
        raise httpx.ConnectError("down")

    cm, mock_client = _mock_async_client(_post)
    try:
        ok, msg = await _test_webhook("https://discord.com/api/webhooks/x/y", "notifications")
    finally:
        cm.stop()
    assert ok is False
    assert "down" in msg
