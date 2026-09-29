"""Coverage tests for backend/routers/settings.py.

Priority (per mission brief): sensitive-field masking in every GET response,
a PUT with the masked sentinel "***" must never overwrite the real secret,
and the SSRF guard on the loopback test endpoint.

Router is mounted standalone (own FastAPI app + isolated SQLite DB) rather
than through the shared `test_client` fixture: settings.py is never reloaded
by conftest.py's test_client fixture, so its `Depends(require_auth)` still
references the pre-reload function object and the global dependency_overrides
key (post-reload auth_mod.require_auth) does not match it — verified
empirically (GET /api/settings via test_client returns 401 with no local
override). Mounting our own app avoids relying on unrelated reload ordering.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from httpx import AsyncClient, ASGITransport

import os
os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")


@pytest_asyncio.fixture
async def settings_client(tmp_path):
    from fastapi import FastAPI
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _ss
    import backend.db.media_servers as _db_ms
    from backend.routers import settings as settings_router

    db_path = str(tmp_path / "settings_test.db")
    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0

    from backend.db.schema import init_db
    await init_db()

    app = FastAPI()
    app.state.scheduler = None
    app.include_router(settings_router.router)
    app.dependency_overrides[settings_router.require_auth] = lambda: "testuser"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c._app_ref = app  # stash for tests that need app.state
        yield c

    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0


# ─── GET /api/settings — masking ───────────────────────────────────────────

async def test_get_settings_masks_sensitive_key(settings_client):
    r = await settings_client.post("/api/settings", json={"discord_webhook": "https://discord.example/hook"})
    assert r.status_code == 200
    r = await settings_client.get("/api/settings")
    assert r.status_code == 200
    assert r.json()["discord_webhook"] == "***"


async def test_get_settings_does_not_mask_unset_sensitive_key(settings_client):
    r = await settings_client.get("/api/settings")
    body = r.json()
    assert body.get("discord_webhook") in (None, "")


async def test_get_settings_masks_api_key_inside_radarr_servers_array(settings_client):
    payload = json.dumps([{"id": "0", "name": "R1", "api_key": "realkey123"}])
    await settings_client.post("/api/settings", json={"radarr_servers": payload})
    r = await settings_client.get("/api/settings")
    servers = json.loads(r.json()["radarr_servers"])
    assert servers[0]["api_key"] == "***"


async def test_get_settings_falls_back_to_full_mask_on_invalid_json_array(settings_client):
    """radarr_servers stored as non-JSON must not raise — mask the whole field."""
    from backend.db.settings_store import set_setting
    await set_setting("radarr_servers", "not-json-at-all")
    r = await settings_client.get("/api/settings")
    assert r.json()["radarr_servers"] == "***"


# ─── POST /api/settings — masked PUT must not overwrite the real secret ───

async def test_put_with_mask_sentinel_does_not_overwrite_existing_secret(settings_client):
    await settings_client.post("/api/settings", json={"discord_webhook": "https://discord.example/real"})
    # Simulate the frontend re-submitting the masked value it was shown
    r = await settings_client.post("/api/settings", json={"discord_webhook": "***"})
    assert r.status_code == 200
    assert "discord_webhook" not in r.json()["updated"]

    from backend.db.settings_store import get_setting
    assert await get_setting("discord_webhook") == "https://discord.example/real"


async def test_put_with_real_new_value_overwrites_existing_secret(settings_client):
    await settings_client.post("/api/settings", json={"discord_webhook": "https://discord.example/old"})
    r = await settings_client.post("/api/settings", json={"discord_webhook": "https://discord.example/new"})
    assert "discord_webhook" in r.json()["updated"]

    from backend.db.settings_store import get_setting
    assert await get_setting("discord_webhook") == "https://discord.example/new"


async def test_put_restores_masked_api_key_inside_radarr_servers_array(settings_client):
    real = json.dumps([{"id": "0", "name": "R1", "api_key": "supersecretkey"}])
    await settings_client.post("/api/settings", json={"radarr_servers": real})

    masked_resubmit = json.dumps([{"id": "0", "name": "R1 renamed", "api_key": "***"}])
    await settings_client.post("/api/settings", json={"radarr_servers": masked_resubmit})

    from backend.db.settings_store import get_setting
    stored = json.loads(await get_setting("radarr_servers"))
    assert stored[0]["api_key"] == "supersecretkey"
    assert stored[0]["name"] == "R1 renamed"


# ─── POST /api/settings — URL scheme validation (SSRF guard) ──────────────

async def test_post_settings_rejects_non_http_scheme(settings_client):
    r = await settings_client.post("/api/settings", json={"radarr_url": "file:///etc/passwd"})
    assert r.status_code == 422
    assert "schéma" in r.json()["detail"]


async def test_post_settings_accepts_masked_url_value_without_validation(settings_client):
    """A masked sensitive URL (qbit_proxy_url is in SENSITIVE_KEYS) must skip scheme validation."""
    r = await settings_client.post("/api/settings", json={"qbit_proxy_url": "***"})
    assert r.status_code == 200


async def test_post_settings_rejects_invalid_backup_path(settings_client):
    with patch("backend.backup._validate_backup_path", side_effect=ValueError("chemin système interdit")):
        r = await settings_client.post("/api/settings", json={"backup_path": "/etc"})
    assert r.status_code == 422
    assert "backup_path" in r.json()["detail"]


async def test_post_settings_reschedules_jobs_when_interval_changes(settings_client):
    with patch("backend._scheduler_instance.reschedule_jobs") as mock_resched:
        r = await settings_client.post("/api/settings", json={"scan_interval_minutes": "120"})
    assert r.status_code == 200
    mock_resched.assert_called_once()
    _, kwargs = mock_resched.call_args
    assert kwargs["scan_minutes"] == 120


async def test_post_settings_does_not_reschedule_when_interval_unchanged(settings_client):
    await settings_client.post("/api/settings", json={"scan_interval_minutes": "120"})
    with patch("backend._scheduler_instance.reschedule_jobs") as mock_resched:
        r = await settings_client.post("/api/settings", json={"scan_interval_minutes": "120"})
    assert r.status_code == 200
    mock_resched.assert_not_called()


async def test_post_settings_invalidates_proxy_whitelist_when_service_url_changes(settings_client):
    with patch("backend.proxy.invalidate_proxy_whitelist") as mock_inv:
        r = await settings_client.post("/api/settings", json={"radarr_url": "http://radarr.local:7878"})
    assert r.status_code == 200
    mock_inv.assert_called_once()


async def test_post_settings_swallows_proxy_whitelist_invalidation_errors(settings_client):
    with patch("backend.proxy.invalidate_proxy_whitelist", side_effect=RuntimeError("boom")):
        r = await settings_client.post("/api/settings", json={"radarr_url": "http://radarr.local:7878"})
    assert r.status_code == 200


async def test_post_settings_ignores_malformed_radarr_servers_json(settings_client):
    """Malformed JSON in radarr_servers must not 500 — the restore-mask step
    catches the exception and falls through to storing the raw incoming value."""
    r = await settings_client.post("/api/settings", json={"radarr_servers": "not-json-at-all"})
    assert r.status_code == 200
    assert "radarr_servers" in r.json()["updated"]


async def test_post_settings_ignores_non_numeric_scan_interval(settings_client):
    r = await settings_client.post("/api/settings", json={"scan_interval_minutes": "not-a-number"})
    assert r.status_code == 200
    assert "scan_interval_minutes" in r.json()["updated"]


async def test_post_settings_ignores_non_numeric_deletion_interval(settings_client):
    await settings_client.post("/api/settings", json={"deletion_check_interval_minutes": "60"})
    r = await settings_client.post("/api/settings", json={"deletion_check_interval_minutes": "not-a-number"})
    assert r.status_code == 200
    assert "deletion_check_interval_minutes" in r.json()["updated"]


async def test_post_settings_enables_backup_job_when_scheduler_present(settings_client):
    from unittest.mock import MagicMock
    mock_sched = MagicMock()
    settings_client._app_ref.state.scheduler = mock_sched
    r = await settings_client.post(
        "/api/settings",
        json={"backup_interval_hours": "12", "backup_enabled": "true"},
    )
    assert r.status_code == 200
    mock_sched.add_job.assert_called_once()
    assert mock_sched.add_job.call_args.kwargs["id"] == "backup_job"


async def test_post_settings_removes_backup_job_when_disabled(settings_client):
    from unittest.mock import MagicMock
    mock_sched = MagicMock()
    settings_client._app_ref.state.scheduler = mock_sched
    r = await settings_client.post(
        "/api/settings",
        json={"backup_interval_hours": "12", "backup_enabled": "false"},
    )
    assert r.status_code == 200
    mock_sched.remove_job.assert_called_once_with("backup_job")


async def test_post_settings_swallows_remove_job_error_when_no_backup_job_exists(settings_client):
    from unittest.mock import MagicMock
    mock_sched = MagicMock()
    mock_sched.remove_job.side_effect = KeyError("no such job")
    settings_client._app_ref.state.scheduler = mock_sched
    r = await settings_client.post(
        "/api/settings",
        json={"backup_interval_hours": "12", "backup_enabled": "false"},
    )
    assert r.status_code == 200


async def test_post_settings_swallows_backup_job_scheduling_errors(settings_client):
    from unittest.mock import MagicMock
    mock_sched = MagicMock()
    mock_sched.add_job.side_effect = RuntimeError("scheduler not started")
    settings_client._app_ref.state.scheduler = mock_sched
    r = await settings_client.post(
        "/api/settings",
        json={"backup_interval_hours": "12", "backup_enabled": "true"},
    )
    assert r.status_code == 200


# ─── Media servers CRUD ─────────────────────────────────────────────────────

async def test_list_media_servers_masks_api_key(settings_client):
    await settings_client.post("/api/settings/media-servers", json={"url": "http://emby:8096", "api_key": "realkey"})
    r = await settings_client.get("/api/settings/media-servers")
    assert r.status_code == 200
    assert r.json()[0]["api_key"] == "***"


async def test_add_media_server_assigns_sequential_id(settings_client):
    r1 = await settings_client.post("/api/settings/media-servers", json={"url": "http://a:1"})
    r2 = await settings_client.post("/api/settings/media-servers", json={"url": "http://b:2"})
    assert r1.status_code == 201
    assert r1.json()["id"] == "0"
    assert r2.json()["id"] == "1"


async def test_add_media_server_rejects_empty_url(settings_client):
    r = await settings_client.post("/api/settings/media-servers", json={"url": ""})
    assert r.status_code == 422


async def test_add_media_server_ignores_non_numeric_existing_id_for_new_id_calc(settings_client, caplog):
    from backend.db.media_servers import save_media_servers
    await save_media_servers([{"id": "not-a-number", "url": "http://x:1", "api_key": ""}])
    r = await settings_client.post("/api/settings/media-servers", json={"url": "http://y:2"})
    assert r.status_code == 201
    # max(existing_ids, default=-1) + 1 with no valid numeric id => "0"
    assert r.json()["id"] == "0"


async def test_update_media_server_masked_api_key_does_not_overwrite(settings_client):
    add = await settings_client.post("/api/settings/media-servers", json={"url": "http://emby:8096", "api_key": "real-secret"})
    sid = add.json()["id"]
    r = await settings_client.put(f"/api/settings/media-servers/{sid}", json={"api_key": "***", "name": "Renamed"})
    assert r.status_code == 200
    updated = next(s for s in r.json()["servers"] if s["id"] == sid)
    assert updated["api_key"] == "real-secret"
    assert updated["name"] == "Renamed"


async def test_update_media_server_sets_real_api_key_and_type(settings_client):
    add = await settings_client.post("/api/settings/media-servers", json={"url": "http://emby:8096"})
    sid = add.json()["id"]
    r = await settings_client.put(
        f"/api/settings/media-servers/{sid}",
        json={"api_key": "brand-new-key", "type": "jellyfin"},
    )
    assert r.status_code == 200
    updated = next(s for s in r.json()["servers"] if s["id"] == sid)
    assert updated["api_key"] == "brand-new-key"
    assert updated["type"] == "jellyfin"


async def test_update_media_server_disabling_purges_queue(settings_client):
    add = await settings_client.post("/api/settings/media-servers", json={"url": "http://emby:8096"})
    sid = add.json()["id"]
    with patch("backend.routers.settings._purge_server_queue", new=AsyncMock(return_value=3)) as mock_purge:
        r = await settings_client.put(f"/api/settings/media-servers/{sid}", json={"enabled": False})
    assert r.status_code == 200
    mock_purge.assert_called_once_with(sid)


async def test_update_media_server_rejects_bad_url_scheme(settings_client):
    add = await settings_client.post("/api/settings/media-servers", json={"url": "http://emby:8096"})
    sid = add.json()["id"]
    r = await settings_client.put(f"/api/settings/media-servers/{sid}", json={"url": "ftp://evil"})
    assert r.status_code == 422


async def test_update_media_server_rejects_bad_ext_url_scheme(settings_client):
    add = await settings_client.post("/api/settings/media-servers", json={"url": "http://emby:8096"})
    sid = add.json()["id"]
    r = await settings_client.put(f"/api/settings/media-servers/{sid}", json={"ext_url": "ftp://evil"})
    assert r.status_code == 422


async def test_update_media_server_enabling_does_not_purge_queue(settings_client):
    add = await settings_client.post("/api/settings/media-servers", json={"url": "http://emby:8096"})
    sid = add.json()["id"]
    with patch("backend.routers.settings._purge_server_queue", new=AsyncMock()) as mock_purge:
        r = await settings_client.put(f"/api/settings/media-servers/{sid}", json={"enabled": True})
    assert r.status_code == 200
    mock_purge.assert_not_called()


async def test_delete_media_server_removes_it_and_purges_queue(settings_client):
    add = await settings_client.post("/api/settings/media-servers", json={"url": "http://emby:8096"})
    sid = add.json()["id"]
    with patch("backend.routers.settings._purge_server_queue", new=AsyncMock(return_value=0)) as mock_purge:
        r = await settings_client.delete(f"/api/settings/media-servers/{sid}")
    assert r.status_code == 200
    assert all(s["id"] != sid for s in r.json()["servers"])
    mock_purge.assert_called_once_with(sid)


async def test_purge_server_queue_endpoint_returns_count(settings_client):
    with patch("backend.db.repositories.delete_pending_by_server", new=AsyncMock(return_value=5)):
        r = await settings_client.post("/api/settings/media-servers/0/purge-queue")
    assert r.status_code == 200
    assert r.json()["purged"] == 5


async def test_reveal_media_server_key_404_when_not_found(settings_client):
    r = await settings_client.get("/api/settings/media-servers/999/reveal")
    assert r.status_code == 404


async def test_reveal_media_server_key_returns_plaintext(settings_client):
    add = await settings_client.post("/api/settings/media-servers", json={"url": "http://emby:8096", "api_key": "plaintext-secret"})
    sid = add.json()["id"]
    r = await settings_client.get(f"/api/settings/media-servers/{sid}/reveal")
    assert r.status_code == 200
    assert r.json()["api_key"] == "plaintext-secret"


# ─── /media-servers/{id}/test — SSRF guard ─────────────────────────────────

async def test_test_media_server_blocks_loopback_host(settings_client):
    add = await settings_client.post("/api/settings/media-servers", json={"url": "http://127.0.0.1:8096"})
    sid = add.json()["id"]
    r = await settings_client.post(f"/api/settings/media-servers/{sid}/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "127.0.0.1" in body["message"]


async def test_test_media_server_routes_plex_type_to_plex_client(settings_client):
    add = await settings_client.post(
        "/api/settings/media-servers",
        json={"url": "http://plex.lan:32400", "type": "plex"},
    )
    sid = add.json()["id"]
    with patch("backend.plex_client.test_plex_server", new=AsyncMock(return_value=(True, "ok", "plex"))):
        r = await settings_client.post(f"/api/settings/media-servers/{sid}/test")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "message": "ok", "server_type": "plex", "error_code": ""}


async def test_test_media_server_routes_non_plex_to_emby_tester(settings_client):
    add = await settings_client.post("/api/settings/media-servers", json={"url": "http://emby.lan:8096"})
    sid = add.json()["id"]
    with patch("backend.routers.settings.test_emby", new=AsyncMock(return_value=(True, "ok", "emby", "E001"))):
        r = await settings_client.post(f"/api/settings/media-servers/{sid}/test")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "message": "ok", "server_type": "emby", "error_code": "E001"}


# ─── /reveal/{key} ──────────────────────────────────────────────────────────

async def test_reveal_setting_403_for_non_sensitive_key(settings_client):
    r = await settings_client.get("/api/settings/reveal/log_level")
    assert r.status_code == 403


async def test_reveal_setting_returns_empty_when_unset(settings_client):
    r = await settings_client.get("/api/settings/reveal/discord_webhook")
    assert r.status_code == 200
    assert r.json() == {"value": ""}


async def test_reveal_setting_parses_json_array_value(settings_client):
    from backend.db.settings_store import set_setting
    await set_setting("radarr_servers", json.dumps([{"id": "0", "api_key": "k"}]))
    r = await settings_client.get("/api/settings/reveal/radarr_servers")
    assert r.status_code == 200
    assert r.json()["value"] == [{"id": "0", "api_key": "k"}]


async def test_reveal_setting_falls_back_to_raw_string_on_invalid_json(settings_client):
    from backend.db.settings_store import set_setting
    await set_setting("radarr_servers", "not-json")
    r = await settings_client.get("/api/settings/reveal/radarr_servers")
    assert r.status_code == 200
    assert r.json()["value"] == "not-json"


async def test_reveal_setting_returns_raw_string_for_non_array_sensitive_key(settings_client):
    from backend.db.settings_store import set_setting
    await set_setting("discord_webhook", "https://discord.example/real")
    r = await settings_client.get("/api/settings/reveal/discord_webhook")
    assert r.status_code == 200
    assert r.json() == {"value": "https://discord.example/real"}


# ─── /test/{service} ────────────────────────────────────────────────────────

async def test_test_service_unknown_service_404(settings_client):
    r = await settings_client.post("/api/settings/test/unknown")
    assert r.status_code == 404


async def test_test_service_returns_tester_result(settings_client):
    # _TESTERS is built once at import time with direct function references —
    # patching the module-level `test_emby` name doesn't reach it; the dict
    # entry itself must be replaced.
    with patch.dict(
        "backend.routers.settings._TESTERS",
        {"emby": AsyncMock(return_value=(True, "connecté"))},
    ):
        r = await settings_client.post("/api/settings/test/emby")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "message": "connecté"}


# ─── /test-arr ──────────────────────────────────────────────────────────────

async def test_test_arr_rejects_bad_scheme(settings_client):
    r = await settings_client.post(
        "/api/settings/test-arr",
        json={"type": "radarr", "url": "ftp://radarr.local", "api_key": "k"},
    )
    assert r.status_code == 422


async def test_test_arr_forwards_valid_request_to_service(settings_client):
    with patch("backend.routers.settings._test_arr", new=AsyncMock(return_value={"ok": True, "message": "v4.7"})) as mock_test:
        r = await settings_client.post(
            "/api/settings/test-arr",
            json={"type": "radarr", "url": "http://radarr.local:7878", "api_key": "k"},
        )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "message": "v4.7"}
    mock_test.assert_called_once_with("radarr", "http://radarr.local:7878", "k")


# ─── /sync-arr-from-seerr ───────────────────────────────────────────────────

async def test_sync_arr_from_seerr_rejects_bad_scheme(settings_client):
    r = await settings_client.post(
        "/api/settings/sync-arr-from-seerr",
        json={"seerr_url": "ftp://seerr.local", "seerr_api_key": "k"},
    )
    assert r.status_code == 422


async def test_sync_arr_from_seerr_maps_value_error_to_400(settings_client):
    with patch("backend.routers.settings._sync_arr", new=AsyncMock(side_effect=ValueError("clé invalide"))):
        r = await settings_client.post(
            "/api/settings/sync-arr-from-seerr",
            json={"seerr_url": "http://seerr.local", "seerr_api_key": "bad"},
        )
    assert r.status_code == 400
    assert "clé invalide" in r.json()["detail"]


async def test_sync_arr_from_seerr_maps_generic_exception_to_502(settings_client):
    with patch("backend.routers.settings._sync_arr", new=AsyncMock(side_effect=RuntimeError("timeout"))):
        r = await settings_client.post(
            "/api/settings/sync-arr-from-seerr",
            json={"seerr_url": "http://seerr.local", "seerr_api_key": "k"},
        )
    assert r.status_code == 502


async def test_sync_arr_from_seerr_returns_service_result_on_success(settings_client):
    with patch("backend.routers.settings._sync_arr", new=AsyncMock(return_value={"imported": 2})):
        r = await settings_client.post(
            "/api/settings/sync-arr-from-seerr",
            json={"seerr_url": "http://seerr.local", "seerr_api_key": "k"},
        )
    assert r.status_code == 200
    assert r.json() == {"imported": 2}
