"""Coverage additions for backend/routers/metrics.py — baseline (with
test_metrics.py) was 52%: the Prometheus /metrics endpoint (lines 20-65)
had zero coverage. That endpoint deliberately has NO require_auth
dependency (it must be scrape-friendly) — access control is instead an
optional bearer-token setting checked by hand, so this file's "auth"
tests exercise that token check directly instead of the usual
require_auth-bypass pattern.
"""
import pytest


@pytest.fixture(autouse=True)
async def _bypass_metrics_router_auth(test_client):
    import backend.routers.metrics as metrics_router_mod
    from backend.db.schema import init_db
    from backend.db.engine import get_db
    from backend.db.settings_store import set_setting, _invalidate_settings_cache

    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM stats_history")
        await db.execute("DELETE FROM job_history")
        await db.execute("DELETE FROM ignored_media")
        await db.commit()
    await set_setting("prometheus_bearer_token", "")
    _invalidate_settings_cache()
    test_client.app.dependency_overrides[metrics_router_mod.require_auth] = lambda: "testuser"
    yield
    test_client.app.dependency_overrides.pop(metrics_router_mod.require_auth, None)


# ─── GET /metrics (Prometheus) ──────────────────────────────────────────────

def test_prometheus_metrics_accessible_without_token_when_none_configured(test_client):
    r = test_client.get("/metrics")
    assert r.status_code == 200
    assert "hygie_media_pending 0" in r.text
    assert "hygie_media_deleted_total 0" in r.text
    assert "hygie_scans_total 0" in r.text


async def test_prometheus_metrics_counts_reflect_db_state(test_client):
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
            ("m1", "T1", "Movie", "lib1", "Library", "/f/x.mkv",
             "2026-01-01T00:00:00+00:00", "2026-01-08T00:00:00+00:00", "pending"),
        )
        await db.execute(
            "INSERT INTO ignored_media (emby_id, title, media_type, ignored_at) "
            "VALUES (?,?,?,?)",
            ("ign1", "Ignored", "Movie", "2026-01-01T00:00:00+00:00"),
        )
        await db.execute(
            "INSERT INTO job_history (job_type, started_at) VALUES ('scan', '2026-01-01T00:00:00+00:00')"
        )
        await db.execute(
            "INSERT INTO job_history (job_type, started_at) VALUES ('deletion_check', '2026-01-01T00:00:00+00:00')"
        )
        await db.commit()

    r = test_client.get("/metrics")
    assert r.status_code == 200
    assert "hygie_media_pending 1" in r.text
    assert "hygie_media_ignored_total 1" in r.text
    assert "hygie_scans_total 1" in r.text
    assert "hygie_deletion_checks_total 1" in r.text


async def test_prometheus_metrics_requires_bearer_when_token_configured(test_client):
    from backend.db.settings_store import set_setting, _invalidate_settings_cache
    await set_setting("prometheus_bearer_token", "sekrit")
    _invalidate_settings_cache()

    r = test_client.get("/metrics")
    assert r.status_code == 401


async def test_prometheus_metrics_rejects_malformed_auth_header(test_client):
    from backend.db.settings_store import set_setting, _invalidate_settings_cache
    await set_setting("prometheus_bearer_token", "sekrit")
    _invalidate_settings_cache()

    r = test_client.get("/metrics", headers={"Authorization": "sekrit"})  # missing "Bearer " prefix
    assert r.status_code == 401


async def test_prometheus_metrics_rejects_wrong_token(test_client):
    from backend.db.settings_store import set_setting, _invalidate_settings_cache
    await set_setting("prometheus_bearer_token", "sekrit")
    _invalidate_settings_cache()

    r = test_client.get("/metrics", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 403


async def test_prometheus_metrics_accepts_correct_token(test_client):
    from backend.db.settings_store import set_setting, _invalidate_settings_cache
    await set_setting("prometheus_bearer_token", "sekrit")
    _invalidate_settings_cache()

    r = test_client.get("/metrics", headers={"Authorization": "Bearer sekrit"})
    assert r.status_code == 200
    assert "hygie_media_pending" in r.text
