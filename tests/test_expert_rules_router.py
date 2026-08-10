"""Endpoint tests for backend/routers/expert_rules.py — previously zero
coverage (found during the 2026-08-10 audit): only import-level references
from conftest.py's fixture setup existed, no actual HTTP-level test.
"""
import pytest


@pytest.fixture(autouse=True)
async def _bypass_expert_rules_router_auth(test_client):
    """Same auth-override staleness as backup.py (see test_backup_router.py) —
    conftest.py's global override doesn't reach backend.routers.expert_rules."""
    import backend.routers.expert_rules as expert_rules_router_mod
    from backend.db.schema import init_db
    from backend.db.engine import get_db
    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM expert_rules")
        await db.commit()
    test_client.app.dependency_overrides[expert_rules_router_mod.require_auth] = lambda: "testuser"
    yield
    test_client.app.dependency_overrides.pop(expert_rules_router_mod.require_auth, None)


def _rule_body(**overrides) -> dict:
    base = {
        "name": "Old unwatched movies",
        "library_id": None,
        "library_ids": None,
        "condition_groups": [
            {
                "conditions": [
                    {"field": "days_not_watched", "op": "gte", "value": 90},
                ],
                "operator": "AND",
            }
        ],
        "operator": "AND",
        "action": "queue",
        "grace_days": 7,
        "enabled": True,
        "priority": 0,
    }
    base.update(overrides)
    return base


def test_list_expert_rules_empty_initially(test_client):
    r = test_client.get("/api/expert-rules")
    assert r.status_code == 200
    assert r.json() == []


def test_create_expert_rule_returns_201_with_assigned_id(test_client):
    r = test_client.post("/api/expert-rules", json=_rule_body())
    assert r.status_code == 201
    body = r.json()
    assert body["id"] is not None
    assert body["name"] == "Old unwatched movies"


def test_create_expert_rule_ignores_client_supplied_id(test_client):
    """id must always be server-assigned on create — a client-sent id must
    force an INSERT, never silently update an unrelated existing row."""
    first = test_client.post("/api/expert-rules", json=_rule_body()).json()
    r = test_client.post("/api/expert-rules", json=_rule_body(id=first["id"], name="Different rule"))
    assert r.status_code == 201
    assert r.json()["id"] != first["id"]


def test_list_expert_rules_after_create(test_client):
    test_client.post("/api/expert-rules", json=_rule_body())
    r = test_client.get("/api/expert-rules")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_update_expert_rule_returns_404_for_unknown_id(test_client):
    r = test_client.put("/api/expert-rules/999999", json=_rule_body())
    assert r.status_code == 404


def test_update_expert_rule_persists_changes(test_client):
    created = test_client.post("/api/expert-rules", json=_rule_body()).json()
    r = test_client.put(f"/api/expert-rules/{created['id']}", json=_rule_body(name="Renamed", grace_days=14))
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"
    assert r.json()["grace_days"] == 14


def test_delete_expert_rule_returns_404_for_unknown_id(test_client):
    r = test_client.delete("/api/expert-rules/999999")
    assert r.status_code == 404


def test_delete_expert_rule_removes_it(test_client):
    created = test_client.post("/api/expert-rules", json=_rule_body()).json()
    r = test_client.delete(f"/api/expert-rules/{created['id']}")
    assert r.status_code == 204

    remaining = test_client.get("/api/expert-rules").json()
    assert all(rule["id"] != created["id"] for rule in remaining)


def test_create_expert_rule_rejects_empty_condition_groups(test_client):
    body = _rule_body()
    body["condition_groups"] = []
    r = test_client.post("/api/expert-rules", json=body)
    assert r.status_code == 422


def test_migrate_from_libraries_endpoint_runs(test_client):
    r = test_client.post("/api/expert-rules/migrate-from-libraries")
    assert r.status_code == 200
    assert "created" in r.json()
