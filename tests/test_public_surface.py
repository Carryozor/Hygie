"""Public surface: /metrics closed without token, /api/version authenticated,
constant 404 body for unknown /api routes, no version on /health."""
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
async def metrics_env(test_client):
    import backend.routers.metrics as metrics_mod
    from backend.db.schema import init_db
    from backend.db.settings_store import set_setting, _invalidate_settings_cache
    await init_db()
    await set_setting("prometheus_bearer_token", "")
    _invalidate_settings_cache()
    metrics_mod._warned_no_token = False
    yield test_client
    metrics_mod._warned_no_token = False


async def test_metrics_404_without_token(metrics_env):
    r = metrics_env.get("/metrics")
    assert r.status_code == 404
    assert r.content == b""


async def test_metrics_warns_once_when_token_missing(metrics_env):
    with patch("backend.db.logs.add_log", new=AsyncMock()) as add_log:
        for _ in range(3):
            assert metrics_env.get("/metrics").status_code == 404
    assert add_log.await_count == 1
    assert "security" in (list(add_log.await_args.args) + list(add_log.await_args.kwargs.values()))


async def test_metrics_still_served_with_token(metrics_env):
    from backend.db.settings_store import set_setting, _invalidate_settings_cache
    await set_setting("prometheus_bearer_token", "sekrit")
    _invalidate_settings_cache()
    assert metrics_env.get("/metrics").status_code == 401
    assert metrics_env.get("/metrics", headers={"Authorization": "Bearer nope"}).status_code == 403
    r = metrics_env.get("/metrics", headers={"Authorization": "Bearer sekrit"})
    assert r.status_code == 200 and "hygie_media_pending" in r.text


def test_api_version_requires_auth(test_client):
    overrides = test_client.app.dependency_overrides
    saved = dict(overrides)
    overrides.clear()
    try:
        assert test_client.get("/api/version").status_code == 401
    finally:
        overrides.update(saved)
    assert test_client.get("/api/version").status_code == 200


def test_unknown_api_route_does_not_reflect_path(test_client):
    r = test_client.get("/api/evil-marker-path")
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}
    assert "evil" not in r.text


def test_health_and_public_routes_untouched(test_client):
    r = test_client.get("/health")
    assert r.status_code in (200, 503)  # 503 = degraded dependency in the test env
    body = r.json()
    assert "version" not in body
    assert {"status", "timestamp", "database", "scheduler"} <= set(body)
