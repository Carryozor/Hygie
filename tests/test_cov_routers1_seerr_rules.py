"""Coverage additions for backend/routers/seerr_rules.py — baseline was
51% (36/74 statements missing): only import-level references existed."""
from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
async def _bypass_seerr_rules_router_auth(test_client):
    import backend.routers.seerr_rules as seerr_rules_router_mod
    from backend.db.schema import init_db
    from backend.db.engine import get_db

    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM seerr_user_rules")
        await db.commit()
    test_client.app.dependency_overrides[seerr_rules_router_mod.require_auth] = lambda: "testuser"
    yield
    test_client.app.dependency_overrides.pop(seerr_rules_router_mod.require_auth, None)


def _rule_body(**overrides) -> dict:
    base = {
        "name": "", "seerr_user_id": 1, "seerr_username": "alice",
        "library_id": "lib1", "library_ids": None, "grace_days": 30,
        "enabled": True, "discord_id": "",
    }
    base.update(overrides)
    return base


async def _fetch_rules_for_user(user_id: int):
    from backend.db.engine import get_db
    async with get_db() as db:
        return await db.fetch_all(
            "SELECT * FROM seerr_user_rules WHERE seerr_user_id=?", (user_id,)
        )


# ─── /users (delegates to seerr_get_users) ─────────────────────────────────

async def test_get_seerr_users_returns_client_result(test_client, monkeypatch):
    import backend.routers.seerr_rules as seerr_rules_mod
    fake = AsyncMock(return_value=[{"id": 1, "username": "alice"}])
    monkeypatch.setattr(seerr_rules_mod, "seerr_get_users", fake)

    r = test_client.get("/api/seerr-rules/users")
    assert r.status_code == 200
    assert r.json() == [{"id": 1, "username": "alice"}]
    fake.assert_awaited_once()


# ─── CRUD on seerr_user_rules ───────────────────────────────────────────────

async def test_list_rules_empty_initially(test_client):
    r = test_client.get("/api/seerr-rules")
    assert r.status_code == 200
    assert r.json() == []


async def test_create_rule_defaults_name_to_username(test_client):
    r = test_client.post("/api/seerr-rules", json=_rule_body(name=""))
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "alice"
    assert body["seerr_user_id"] == 1
    assert body["grace_days"] == 30
    assert body["enabled"] == 1


async def test_create_rule_uses_explicit_name_when_given(test_client):
    r = test_client.post("/api/seerr-rules", json=_rule_body(name="Custom Name"))
    assert r.json()["name"] == "Custom Name"


async def test_create_rule_persists_library_ids_as_json(test_client):
    r = test_client.post("/api/seerr-rules", json=_rule_body(library_ids=["libA", "libB"]))
    assert r.status_code == 200
    body = r.json()
    import json
    assert json.loads(body["library_ids"]) == ["libA", "libB"]


async def test_create_rule_library_ids_none_means_all(test_client):
    r = test_client.post("/api/seerr-rules", json=_rule_body(library_ids=None))
    assert r.status_code == 200
    assert r.json()["library_ids"] is None


async def test_update_rule_404_when_missing(test_client):
    r = test_client.put("/api/seerr-rules/999999", json=_rule_body())
    assert r.status_code == 404


async def test_update_rule_changes_grace_days_and_enabled(test_client):
    created = test_client.post("/api/seerr-rules", json=_rule_body(grace_days=30, enabled=True)).json()
    rule_id = created["id"]

    r = test_client.put(
        f"/api/seerr-rules/{rule_id}",
        json=_rule_body(grace_days=90, enabled=False, discord_id="12345"),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["grace_days"] == 90
    assert body["enabled"] == 0
    assert body["discord_id"] == "12345"


async def test_update_rule_only_affects_targeted_row(test_client):
    r1 = test_client.post("/api/seerr-rules", json=_rule_body(seerr_user_id=1, seerr_username="alice", library_id="libA"))
    r2 = test_client.post("/api/seerr-rules", json=_rule_body(seerr_user_id=2, seerr_username="bob", library_id="libB"))
    id1, id2 = r1.json()["id"], r2.json()["id"]

    test_client.put(f"/api/seerr-rules/{id1}", json=_rule_body(seerr_username="alice", grace_days=99))

    updated_other = test_client.get("/api/seerr-rules").json()
    other = next(row for row in updated_other if row["id"] == id2)
    assert other["grace_days"] == 30  # untouched


async def test_delete_rule_removes_row(test_client):
    created = test_client.post("/api/seerr-rules", json=_rule_body()).json()
    rule_id = created["id"]

    r = test_client.delete(f"/api/seerr-rules/{rule_id}")
    assert r.status_code == 200
    assert r.json() == {"status": "deleted"}

    remaining = test_client.get("/api/seerr-rules").json()
    assert all(row["id"] != rule_id for row in remaining)


async def test_delete_rule_nonexistent_id_still_returns_deleted_status(test_client):
    """DELETE is idempotent by design (no existence check) — document the
    current behavior rather than assert an unverified 404."""
    r = test_client.delete("/api/seerr-rules/999999")
    assert r.status_code == 200
    assert r.json() == {"status": "deleted"}


# ─── discord-mappings ───────────────────────────────────────────────────────

async def test_get_discord_mappings_empty_initially(test_client):
    r = test_client.get("/api/seerr-rules/discord-mappings")
    assert r.status_code == 200
    assert r.json() == []


async def test_save_discord_mapping_inserts_new_global_row_when_none_exists(test_client):
    r = test_client.post(
        "/api/seerr-rules/discord-mappings",
        json={"seerr_user_id": 5, "seerr_username": "carol", "discord_id": "999"},
    )
    assert r.status_code == 200
    assert r.json() == {"status": "saved"}

    rows = await _fetch_rules_for_user(5)
    assert len(rows) == 1
    assert rows[0]["discord_id"] == "999"
    assert rows[0]["library_id"] == "*"


async def test_save_discord_mapping_updates_existing_rows_for_user(test_client):
    """When rows already exist for this seerr_user_id (per-library rules),
    saving a mapping must update discord_id on ALL of them, not insert a
    new duplicate row."""
    test_client.post("/api/seerr-rules", json=_rule_body(seerr_user_id=7, seerr_username="dave", library_id="libA"))
    test_client.post("/api/seerr-rules", json=_rule_body(seerr_user_id=7, seerr_username="dave", library_id="libB"))

    r = test_client.post(
        "/api/seerr-rules/discord-mappings",
        json={"seerr_user_id": 7, "seerr_username": "dave", "discord_id": "777"},
    )
    assert r.status_code == 200

    rows = await _fetch_rules_for_user(7)
    assert len(rows) == 2  # no new row inserted
    assert all(row["discord_id"] == "777" for row in rows)


async def test_save_discord_mapping_does_not_affect_other_users(test_client):
    test_client.post("/api/seerr-rules", json=_rule_body(seerr_user_id=1, seerr_username="alice"))
    test_client.post("/api/seerr-rules", json=_rule_body(seerr_user_id=2, seerr_username="bob"))

    test_client.post(
        "/api/seerr-rules/discord-mappings",
        json={"seerr_user_id": 1, "seerr_username": "alice", "discord_id": "111"},
    )

    bob_rows = await _fetch_rules_for_user(2)
    assert bob_rows[0]["discord_id"] != "111"


# ─── AUTH ───────────────────────────────────────────────────────────────────

def test_list_rules_requires_auth(test_client):
    import backend.routers.seerr_rules as seerr_rules_mod
    override = test_client.app.dependency_overrides.pop(seerr_rules_mod.require_auth, None)
    try:
        r = test_client.get("/api/seerr-rules")
        assert r.status_code == 401
    finally:
        if override is not None:
            test_client.app.dependency_overrides[seerr_rules_mod.require_auth] = override
