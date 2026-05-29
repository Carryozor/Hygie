"""API tests for /api/expert-rules CRUD endpoints."""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

import backend.db.utils as _db_utils
import backend.db.schema as _db_schema
import backend.routers.expert_rules as _er_router
from backend.main import app
from backend.db.schema import init_db
from backend.auth import require_auth


# ─── DB isolation fixture ─────────────────────────────────────────────────────

@pytest_asyncio.fixture(autouse=True)
async def patch_db(monkeypatch, tmp_path):
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", path)
    monkeypatch.setattr(_db_schema, "DB_PATH", path)
    monkeypatch.setattr(_er_router, "DB_PATH", path)
    await init_db()


# ─── Auth override fixture ────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def auth_client():
    """AsyncClient with auth dependency overridden to return 'testuser'."""
    app.dependency_overrides[require_auth] = lambda: "testuser"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.pop(require_auth, None)


@pytest_asyncio.fixture
async def no_auth_client():
    """AsyncClient with NO auth override — uses real require_auth (no token → 401)."""
    app.dependency_overrides.pop(require_auth, None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ─── Payload ──────────────────────────────────────────────────────────────────

_PAYLOAD = {
    "name": "Old movies",
    "conditions": [{"field": "days_not_watched", "op": "gt", "value": 365}],
    "operator": "AND",
    "action": "queue",
    "enabled": True,
    "priority": 0,
}


# ─── Tests ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_expert_rule(auth_client):
    r = await auth_client.post("/api/expert-rules", json=_PAYLOAD)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["id"] is not None and data["id"] > 0
    assert data["name"] == "Old movies"


@pytest.mark.asyncio
async def test_list_expert_rules(auth_client):
    # Create one rule first
    await auth_client.post("/api/expert-rules", json=_PAYLOAD)

    r = await auth_client.get("/api/expert-rules")
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["name"] == "Old movies"


@pytest.mark.asyncio
async def test_delete_expert_rule(auth_client):
    # Create then delete
    create_r = await auth_client.post("/api/expert-rules", json=_PAYLOAD)
    assert create_r.status_code == 201
    rule_id = create_r.json()["id"]

    del_r = await auth_client.delete(f"/api/expert-rules/{rule_id}")
    assert del_r.status_code == 204, del_r.text

    list_r = await auth_client.get("/api/expert-rules")
    assert list_r.status_code == 200
    assert list_r.json() == []


@pytest.mark.asyncio
async def test_unauthorized_returns_401(no_auth_client):
    r = await no_auth_client.get("/api/expert-rules")
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_update_expert_rule(auth_client):
    create_r = await auth_client.post("/api/expert-rules", json=_PAYLOAD)
    rule_id = create_r.json()["id"]
    updated_payload = {**_PAYLOAD, "name": "Updated name", "priority": 5}
    r = await auth_client.put(f"/api/expert-rules/{rule_id}", json=updated_payload)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["name"] == "Updated name"
    assert data["priority"] == 5


@pytest.mark.asyncio
async def test_update_expert_rule_not_found(auth_client):
    r = await auth_client.put("/api/expert-rules/9999", json=_PAYLOAD)
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_delete_expert_rule_not_found(auth_client):
    r = await auth_client.delete("/api/expert-rules/9999")
    assert r.status_code == 404, r.text
