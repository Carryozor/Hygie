"""Coverage additions for backend/routers/libraries.py — baseline was 41%
(99/169 statements missing). CRUD for library rules plus Emby/Plex
listing and scan/reevaluate triggers; the update/scan endpoints schedule
background work that later drives real deletions, so tests assert the
exact args those background tasks receive, not just the HTTP status.
"""
import json

from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
async def _bypass_libraries_router_auth(test_client):
    """Same auth-override staleness as ignored.py/calendar.py/unmonitored.py
    (conftest's global override targets the post-reload auth_mod.require_auth,
    but this router was imported — and its routes bound — before that reload)."""
    import backend.routers.libraries as libraries_router_mod
    from backend.db.schema import init_db
    from backend.db.engine import get_db

    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM libraries")
        await db.commit()
    test_client.app.dependency_overrides[libraries_router_mod.require_auth] = lambda: "testuser"
    yield
    test_client.app.dependency_overrides.pop(libraries_router_mod.require_auth, None)


async def _seed_library(**overrides) -> str:
    from backend.db.engine import get_db
    from datetime import datetime, timezone
    import uuid
    lib_id = overrides.get("id", str(uuid.uuid4()))
    row = {
        "id": lib_id, "name": "Movies", "emby_library_id": "emby-1", "server_id": "0",
        "conditions": json.dumps([{"field": "age_days", "op": "gt", "value": 30}]),
        "logic": "AND", "grace_days": 7, "seerr_conditions": "[]", "enabled": 1,
        "deletion_unit": "episode", "created_at": datetime.now(timezone.utc).isoformat(),
    }
    row.update(overrides)
    async with get_db() as db:
        await db.execute(
            "INSERT INTO libraries (id, name, emby_library_id, server_id, conditions, logic, "
            "grace_days, seerr_conditions, enabled, deletion_unit, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (row["id"], row["name"], row["emby_library_id"], row["server_id"], row["conditions"],
             row["logic"], row["grace_days"], row["seerr_conditions"], row["enabled"],
             row["deletion_unit"], row["created_at"]),
        )
        await db.commit()
    return lib_id


async def _fetch_library(lib_id: str):
    from backend.db.engine import get_db
    async with get_db() as db:
        return await db.fetch_one("SELECT * FROM libraries WHERE id=?", (lib_id,))


# ─── list_emby_libraries ────────────────────────────────────────────────────

async def test_list_emby_libraries_maps_id_and_name(test_client, monkeypatch):
    import backend.routers.libraries as lib_mod
    fake = AsyncMock(return_value=[{"Id": "1", "Name": "Movies", "Extra": "ignored"}])
    monkeypatch.setattr(lib_mod, "emby_get_libraries", fake)

    r = test_client.get("/api/libraries/emby-libraries?server_id=2")
    assert r.status_code == 200
    assert r.json() == [{"id": "1", "name": "Movies"}]
    fake.assert_awaited_once_with("2")


async def test_list_emby_libraries_alias_route_same_behavior(test_client, monkeypatch):
    import backend.routers.libraries as lib_mod
    monkeypatch.setattr(lib_mod, "emby_get_libraries", AsyncMock(return_value=[]))
    r = test_client.get("/api/libraries/emby")
    assert r.status_code == 200
    assert r.json() == []


# ─── list_plex_sections ─────────────────────────────────────────────────────

async def test_plex_sections_404_when_server_not_plex(test_client, monkeypatch):
    import backend.db.media_servers as ms_mod
    monkeypatch.setattr(ms_mod, "get_media_servers", AsyncMock(return_value=[
        {"id": "1", "type": "emby"},
    ]))
    r = test_client.get("/api/libraries/plex/1/sections")
    assert r.status_code == 404


async def test_plex_sections_404_when_server_id_unknown(test_client, monkeypatch):
    import backend.db.media_servers as ms_mod
    monkeypatch.setattr(ms_mod, "get_media_servers", AsyncMock(return_value=[]))
    r = test_client.get("/api/libraries/plex/999/sections")
    assert r.status_code == 404


async def test_plex_sections_400_when_client_cannot_be_built(test_client, monkeypatch):
    import backend.db.media_servers as ms_mod
    monkeypatch.setattr(ms_mod, "get_media_servers", AsyncMock(return_value=[
        {"id": "1", "type": "plex", "url": "", "api_key": ""},
    ]))
    monkeypatch.setattr("backend.plex_client.build_plex_client", lambda server: None)
    r = test_client.get("/api/libraries/plex/1/sections")
    assert r.status_code == 400


async def test_plex_sections_502_when_plex_raises(test_client, monkeypatch):
    import backend.db.media_servers as ms_mod
    monkeypatch.setattr(ms_mod, "get_media_servers", AsyncMock(return_value=[
        {"id": "1", "type": "plex", "url": "http://plex", "api_key": "tok"},
    ]))
    fake_plex = AsyncMock()
    fake_plex.get_libraries = AsyncMock(side_effect=RuntimeError("timeout"))
    monkeypatch.setattr("backend.plex_client.build_plex_client", lambda server: fake_plex)
    r = test_client.get("/api/libraries/plex/1/sections")
    assert r.status_code == 502


async def test_plex_sections_flags_already_configured(test_client, monkeypatch):
    await _seed_library(server_id="1", emby_library_id="sec-A")
    import backend.db.media_servers as ms_mod
    monkeypatch.setattr(ms_mod, "get_media_servers", AsyncMock(return_value=[
        {"id": "1", "type": "plex", "url": "http://plex", "api_key": "tok"},
    ]))
    fake_plex = AsyncMock()
    fake_plex.get_libraries = AsyncMock(return_value=[
        {"id": "sec-A", "title": "Configured", "type": "movie"},
        {"id": "sec-B", "title": "Not configured", "type": "show"},
    ])
    monkeypatch.setattr("backend.plex_client.build_plex_client", lambda server: fake_plex)

    r = test_client.get("/api/libraries/plex/1/sections")
    assert r.status_code == 200
    body = {s["id"]: s["configured"] for s in r.json()}
    assert body == {"sec-A": True, "sec-B": False}


# ─── list/create/update/delete/clone ────────────────────────────────────────

async def test_list_libraries_empty_initially(test_client):
    r = test_client.get("/api/libraries")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_libraries_parses_json_fields_and_enabled_bool(test_client):
    await _seed_library(name="Shows", enabled=0)
    r = test_client.get("/api/libraries")
    row = r.json()[0]
    assert row["name"] == "Shows"
    assert row["enabled"] is False
    assert isinstance(row["conditions"], list)
    assert row["conditions"][0]["field"] == "age_days"


async def test_create_library_persists_and_logs(test_client):
    body = {
        "name": "New Lib", "emby_library_id": "e-9",
        "conditions": [{"field": "size_gb", "op": "gte", "value": 5}],
        "grace_days": 14,
    }
    r = test_client.post("/api/libraries", json=body)
    assert r.status_code == 200
    lib_id = r.json()["id"]

    row = await _fetch_library(lib_id)
    assert row["name"] == "New Lib"
    assert row["grace_days"] == 14
    assert json.loads(row["conditions"]) == [{"field": "size_gb", "op": "gte", "value": 5}]

    from backend.db.engine import get_db
    async with get_db() as db:
        logs = await db.fetch_all("SELECT message FROM logs WHERE source='library' ORDER BY id DESC LIMIT 5")
    assert any("New Lib" in row["message"] for row in logs)


async def test_create_library_rejects_empty_name(test_client):
    r = test_client.post("/api/libraries", json={"name": "", "emby_library_id": "e-1"})
    assert r.status_code == 422


async def test_update_library_no_changes_returns_status(test_client):
    lib_id = await _seed_library()
    r = test_client.put(f"/api/libraries/{lib_id}", json={})
    assert r.status_code == 200
    assert r.json() == {"status": "no_changes"}


async def test_update_library_persists_new_grace_days_without_reevaluating(test_client, monkeypatch):
    import backend.routers.libraries as lib_mod
    fake_reeval = AsyncMock(return_value=0)
    monkeypatch.setattr(lib_mod, "reevaluate_library_queue", fake_reeval)

    lib_id = await _seed_library(grace_days=7)
    r = test_client.put(f"/api/libraries/{lib_id}", json={"grace_days": 21})
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

    row = await _fetch_library(lib_id)
    assert row["grace_days"] == 21
    fake_reeval.assert_not_called()


async def test_update_library_conditions_change_triggers_background_reevaluate(test_client, monkeypatch):
    import backend.routers.libraries as lib_mod
    fake_reeval = AsyncMock(return_value=2)
    monkeypatch.setattr(lib_mod, "reevaluate_library_queue", fake_reeval)

    lib_id = await _seed_library()
    r = test_client.put(
        f"/api/libraries/{lib_id}",
        json={"conditions": [{"field": "age_days", "op": "gt", "value": 99}]},
    )
    assert r.status_code == 200
    fake_reeval.assert_awaited_once_with(lib_id)

    row = await _fetch_library(lib_id)
    assert json.loads(row["conditions"]) == [{"field": "age_days", "op": "gt", "value": 99}]


async def test_update_library_seerr_conditions_and_enabled_flag(test_client, monkeypatch):
    import backend.routers.libraries as lib_mod
    monkeypatch.setattr(lib_mod, "reevaluate_library_queue", AsyncMock(return_value=0))

    lib_id = await _seed_library(enabled=1)
    r = test_client.put(
        f"/api/libraries/{lib_id}",
        json={
            "seerr_conditions": [{"type": "user_exclude", "user_id": 7, "username": "bob"}],
            "enabled": False,
        },
    )
    assert r.status_code == 200

    row = await _fetch_library(lib_id)
    assert row["enabled"] == 0
    assert json.loads(row["seerr_conditions"]) == [{"type": "user_exclude", "user_id": 7, "username": "bob"}]


async def test_delete_library_removes_row_and_logs(test_client):
    lib_id = await _seed_library()
    r = test_client.delete(f"/api/libraries/{lib_id}")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert await _fetch_library(lib_id) is None


async def test_clone_library_404_when_source_missing(test_client):
    r = test_client.post("/api/libraries/does-not-exist/clone")
    assert r.status_code == 404


async def test_clone_library_copies_fields_with_suffix(test_client):
    lib_id = await _seed_library(name="Original", grace_days=10)
    r = test_client.post(f"/api/libraries/{lib_id}/clone")
    assert r.status_code == 200
    new_id = r.json()["id"]
    assert new_id != lib_id

    clone = await _fetch_library(new_id)
    assert clone["name"] == "Original (copie)"
    assert clone["grace_days"] == 10
    assert clone["emby_library_id"] == "emby-1"


# ─── scan / scan-multi / reevaluate ─────────────────────────────────────────

async def test_scan_library_409_when_scan_already_running(test_client, monkeypatch):
    import backend.routers.libraries as lib_mod
    monkeypatch.setattr(lib_mod, "is_scan_running", lambda: True)
    r = test_client.post("/api/libraries/lib-1/scan")
    assert r.status_code == 409


async def test_scan_library_schedules_background_scan(test_client, monkeypatch):
    import backend.routers.libraries as lib_mod
    monkeypatch.setattr(lib_mod, "is_scan_running", lambda: False)
    fake_scan = AsyncMock()
    monkeypatch.setattr(lib_mod, "run_scan_library", fake_scan)

    r = test_client.post("/api/libraries/lib-42/scan")
    assert r.status_code == 200
    assert r.json() == {"status": "started"}
    fake_scan.assert_awaited_once_with("lib-42")


async def test_scan_multi_409_when_scan_already_running(test_client, monkeypatch):
    import backend.routers.libraries as lib_mod
    monkeypatch.setattr(lib_mod, "is_scan_running", lambda: True)
    r = test_client.post("/api/libraries/scan-multi", json={"library_ids": ["a"]})
    assert r.status_code == 409


async def test_scan_multi_nothing_to_scan_when_ids_all_falsy(test_client, monkeypatch):
    import backend.routers.libraries as lib_mod
    monkeypatch.setattr(lib_mod, "is_scan_running", lambda: False)
    r = test_client.post("/api/libraries/scan-multi", json={"library_ids": ["", None]})
    assert r.status_code == 200
    assert r.json() == {"status": "nothing_to_scan"}


async def test_scan_multi_filters_falsy_ids_and_schedules(test_client, monkeypatch):
    import backend.routers.libraries as lib_mod
    monkeypatch.setattr(lib_mod, "is_scan_running", lambda: False)
    fake_scan_multi = AsyncMock()
    monkeypatch.setattr(lib_mod, "run_scan_libraries", fake_scan_multi)

    r = test_client.post("/api/libraries/scan-multi", json={"library_ids": ["a", "", None, "b"]})
    assert r.status_code == 200
    assert r.json() == {"status": "started", "library_ids": ["a", "b"]}
    fake_scan_multi.assert_awaited_once_with(["a", "b"])


async def test_reevaluate_returns_removed_count(test_client, monkeypatch):
    import backend.routers.libraries as lib_mod
    monkeypatch.setattr(lib_mod, "reevaluate_library_queue", AsyncMock(return_value=3))
    r = test_client.post("/api/libraries/lib-1/reevaluate")
    assert r.status_code == 200
    assert r.json() == {"removed": 3}


async def test_reevaluate_503_when_media_server_unreachable(test_client, monkeypatch):
    import backend.routers.libraries as lib_mod
    from backend.exceptions import MediaServerUnreachable
    monkeypatch.setattr(
        lib_mod, "reevaluate_library_queue",
        AsyncMock(side_effect=MediaServerUnreachable("down")),
    )
    r = test_client.post("/api/libraries/lib-1/reevaluate")
    assert r.status_code == 503
    assert "injoignable" in r.json()["detail"]


# ─── test/{service} alias ───────────────────────────────────────────────────

async def test_test_service_alias_404_for_unknown_service(test_client):
    r = test_client.post("/api/libraries/test/not-a-real-service")
    assert r.status_code == 404


async def test_test_service_alias_delegates_to_tester_and_returns_result(test_client, monkeypatch):
    import backend.routers.settings as settings_mod
    fake_tester = AsyncMock(return_value=(True, "Connexion OK"))
    monkeypatch.setitem(settings_mod._TESTERS, "emby", fake_tester)

    r = test_client.post("/api/libraries/test/emby")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "message": "Connexion OK"}
    fake_tester.assert_awaited_once()


async def test_test_service_alias_returns_failure_result_unchanged(test_client, monkeypatch):
    import backend.routers.settings as settings_mod
    fake_tester = AsyncMock(return_value=(False, "Clé API invalide"))
    monkeypatch.setitem(settings_mod._TESTERS, "radarr", fake_tester)

    r = test_client.post("/api/libraries/test/radarr")
    assert r.status_code == 200
    assert r.json() == {"ok": False, "message": "Clé API invalide"}


# ─── AUTH ───────────────────────────────────────────────────────────────────

def test_list_libraries_requires_auth(test_client):
    import backend.routers.libraries as lib_mod
    override = test_client.app.dependency_overrides.pop(lib_mod.require_auth, None)
    try:
        r = test_client.get("/api/libraries")
        assert r.status_code == 401
    finally:
        if override is not None:
            test_client.app.dependency_overrides[lib_mod.require_auth] = override
