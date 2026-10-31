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


# ─── 4.5: backup + job freshness gauges ─────────────────────────────────────

def _insert_job(job_type, finished_at, status):
    async def _go():
        from backend.db.engine import get_db
        async with get_db() as db:
            await db.execute(
                "INSERT INTO job_history (job_type, started_at, finished_at, status) VALUES (?,?,?,?)",
                (job_type, finished_at, finished_at, status),
            )
            await db.commit()
    return _go()


async def test_metrics_expose_last_finished_job_per_type(test_client):
    await _insert_job("scan", "2026-01-01T00:00:00+00:00", "error")
    await _insert_job("scan", "2026-01-02T00:00:00+00:00", "success")
    await _insert_job("deletion_check", "2026-01-03T00:00:00+00:00", "error")
    text = test_client.get("/metrics").text
    # newest finished run wins, its outcome is a 0/1 gauge
    assert 'hygie_job_last_finished_timestamp_seconds{job="scan"} 1767312000' in text
    assert 'hygie_job_last_success{job="scan"} 1' in text
    assert 'hygie_job_last_success{job="deletion_check"} 0' in text


async def test_metrics_job_series_absent_when_job_never_finished(test_client):
    text = test_client.get("/metrics").text
    assert "hygie_job_last_finished_timestamp_seconds{" not in text


async def test_metrics_backup_gauges_reflect_backup_status(test_client, monkeypatch):
    import backend.routers.metrics as m

    async def fake():
        return {"state": "stale", "interval_hours": 24, "last_backup_at": "x", "age_hours": 700.0}
    monkeypatch.setattr(m, "backup_status", fake)
    text = test_client.get("/metrics").text
    assert "hygie_backup_age_seconds 2520000.0" in text
    assert "hygie_backup_ok 0" in text


async def test_metrics_backup_ok_when_recent_and_unknown_when_disabled(test_client, monkeypatch):
    import backend.routers.metrics as m

    async def ok():
        return {"state": "ok", "interval_hours": 24, "last_backup_at": "x", "age_hours": 1.0}
    monkeypatch.setattr(m, "backup_status", ok)
    assert "hygie_backup_ok 1" in test_client.get("/metrics").text

    async def off():
        return {"state": "disabled", "interval_hours": 24, "last_backup_at": None, "age_hours": None}
    monkeypatch.setattr(m, "backup_status", off)
    text = test_client.get("/metrics").text
    assert "hygie_backup_ok" not in text and "hygie_backup_age_seconds" not in text


async def test_metrics_survive_backup_status_failure(test_client, monkeypatch):
    import backend.routers.metrics as m

    async def boom():
        raise RuntimeError("disk")
    monkeypatch.setattr(m, "backup_status", boom)
    r = test_client.get("/metrics")
    assert r.status_code == 200 and "hygie_media_pending" in r.text
