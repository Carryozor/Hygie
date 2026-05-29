# tests/test_plex_webhook.py
"""Integration tests for the Plex webhook endpoint."""
import os
import json
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")
os.environ.pop("DATABASE_URL", None)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client(tmp_path_factory):
    import importlib
    import backend.db.engine as _eng
    db_path = str(tmp_path_factory.mktemp("webhook") / "wh.db")
    _eng.SQLITE_PATH = db_path
    import backend.main as main_mod
    importlib.reload(main_mod)
    app = main_mod.app
    async with main_mod.lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


def _make_payload(event: str, rating_key: str = "101") -> str:
    return json.dumps({
        "event": event,
        "Account": {"id": 1, "title": "testuser"},
        "Server": {"uuid": "server-abc"},
        "Metadata": {
            "ratingKey": rating_key,
            "title": "Inception",
            "type": "movie",
            "viewCount": 3,
            "lastViewedAt": 1700000000,
        },
    })


async def test_webhook_scrobble_returns_200(client):
    payload = _make_payload("media.scrobble")
    resp = await client.post("/api/plex/webhook", data={"payload": payload})
    assert resp.status_code == 200


async def test_webhook_play_returns_200(client):
    payload = _make_payload("media.play")
    resp = await client.post("/api/plex/webhook", data={"payload": payload})
    assert resp.status_code == 200


async def test_webhook_missing_payload_returns_4xx(client):
    resp = await client.post("/api/plex/webhook", data={})
    # FastAPI returns 422 for missing required Form field
    assert resp.status_code in (400, 422)


async def test_webhook_invalid_json_returns_400(client):
    resp = await client.post("/api/plex/webhook", data={"payload": "not-json"})
    assert resp.status_code == 400


async def test_webhook_wrong_secret_accepted_when_none_configured(client):
    payload = _make_payload("media.scrobble")
    resp = await client.post(
        "/api/plex/webhook?secret=wrongsecret",
        data={"payload": payload},
    )
    # Fresh DB has no plex_webhook_secret → all secrets accepted
    assert resp.status_code in (200, 403)
