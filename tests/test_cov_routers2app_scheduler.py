"""Coverage tests for backend/routers/scheduler.py — job triggers and history.

Uses the shared `test_client` fixture from conftest.py: unlike settings.py /
storage.py / database.py, backend.routers.scheduler IS reloaded by that
fixture (`importlib.reload(_sched_router_mod)`) right after auth_mod is
reloaded, so its `Depends(require_auth)` matches the fixture's global
dependency_overrides key — verified empirically (GET /api/scheduler/status
via test_client returns 200 with no extra override needed).
"""
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
async def _clean_job_history(test_client):
    from backend.db.schema import init_db
    from backend.db.engine import get_db
    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM job_history")
        await db.commit()
    yield


def test_scan_trigger_starts_when_idle(test_client):
    with patch("backend.routers.scheduler.is_scan_running", return_value=False):
        r = test_client.post("/api/scan/trigger")
    assert r.status_code == 200
    assert r.json() == {"status": "started"}


def test_scan_trigger_returns_409_when_already_running(test_client):
    with patch("backend.routers.scheduler.is_scan_running", return_value=True):
        r = test_client.post("/api/scan/trigger")
    assert r.status_code == 409
    assert "Scan" in r.json()["error"]


def test_deletion_trigger_starts_when_idle(test_client):
    with patch("backend.routers.scheduler.is_deletion_running", return_value=False):
        r = test_client.post("/api/deletion/trigger")
    assert r.status_code == 200


def test_deletion_trigger_returns_409_when_already_running(test_client):
    with patch("backend.routers.scheduler.is_deletion_running", return_value=True):
        r = test_client.post("/api/deletion/trigger")
    assert r.status_code == 409
    assert "Deletion" in r.json()["error"]


def test_scan_library_trigger_returns_409_when_scan_running(test_client):
    with patch("backend.routers.scheduler.is_scan_running", return_value=True):
        r = test_client.post("/api/scan/library/lib-1")
    assert r.status_code == 409


def test_scan_library_trigger_starts_when_idle(test_client):
    with patch("backend.routers.scheduler.is_scan_running", return_value=False):
        r = test_client.post("/api/scan/library/lib-1")
    assert r.status_code == 200
    assert r.json() == {"status": "started"}


def test_scheduler_run_unknown_job_returns_404(test_client):
    r = test_client.post("/api/scheduler/run/does-not-exist")
    assert r.status_code == 404


def test_scheduler_run_scan_returns_409_when_scan_running(test_client):
    with patch("backend.routers.scheduler.is_scan_running", return_value=True):
        r = test_client.post("/api/scheduler/run/scan")
    assert r.status_code == 409


def test_scheduler_run_deletion_returns_409_when_deletion_running(test_client):
    with patch("backend.routers.scheduler.is_deletion_running", return_value=True):
        r = test_client.post("/api/scheduler/run/deletion")
    assert r.status_code == 409


def test_scheduler_run_collection_sync_starts(test_client):
    r = test_client.post("/api/scheduler/run/collection_sync")
    assert r.status_code == 200
    assert r.json() == {"status": "started"}


def test_emby_collection_sync_endpoint_starts(test_client):
    r = test_client.post("/api/emby-collection/sync")
    assert r.status_code == 200
    assert r.json() == {"status": "started"}


def test_scheduler_status_lists_jobs_with_running_flags(test_client):
    with patch("backend.routers.scheduler.is_scan_running", return_value=True), \
         patch("backend.routers.scheduler.is_deletion_running", return_value=False):
        r = test_client.get("/api/scheduler/status")
    assert r.status_code == 200
    jobs = {j["id"]: j for j in r.json()}
    assert jobs["scan_job"]["is_running"] is True
    assert jobs["deletion_job"]["is_running"] is False
    assert jobs["scan_job"]["next_run"] is not None


def test_media_job_status_reports_running_flags(test_client):
    with patch("backend.routers.scheduler.is_scan_running", return_value=True), \
         patch("backend.routers.scheduler.is_deletion_running", return_value=False):
        r = test_client.get("/api/media/job-status")
    assert r.status_code == 200
    assert r.json() == {"scan_running": True, "deletion_running": False}


# ─── /api/jobs/history — dedup logic ───────────────────────────────────────

async def _insert_job_run(job_type, started_at, status="success", message="ok"):
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO job_history (job_type, started_at, finished_at, status, message) "
            "VALUES (?, ?, ?, ?, ?)",
            (job_type, started_at, started_at, status, message),
        )
        await db.commit()


async def test_jobs_history_deduplicates_same_type_and_minute(test_client):
    await _insert_job_run("scan", "2026-01-01T10:00:05")
    await _insert_job_run("scan", "2026-01-01T10:00:45")  # same type, same YYYY-MM-DDTHH:MM
    r = test_client.get("/api/jobs/history")
    assert r.status_code == 200
    scans = [row for row in r.json() if row["job_type"] == "scan"]
    assert len(scans) == 1


async def test_jobs_history_keeps_distinct_minutes(test_client):
    await _insert_job_run("scan", "2026-01-01T10:00:05")
    await _insert_job_run("scan", "2026-01-01T10:05:05")
    r = test_client.get("/api/jobs/history")
    scans = [row for row in r.json() if row["job_type"] == "scan"]
    assert len(scans) == 2


async def test_jobs_history_maps_job_name_result_and_status_fields(test_client):
    await _insert_job_run("deletion", "2026-01-01T11:00:00", status="error", message="boom")
    r = test_client.get("/api/jobs/history")
    row = next(x for x in r.json() if x["job_type"] == "deletion")
    assert row["job_name"] == "deletion"
    assert row["result"] == "boom"
    assert row["status"] == "error"


async def test_jobs_history_defaults_status_to_interrupted_when_null(test_client):
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO job_history (job_type, started_at, finished_at, status, message) "
            "VALUES (?, ?, NULL, NULL, NULL)",
            ("scan", "2026-02-01T00:00:00"),
        )
        await db.commit()
    r = test_client.get("/api/jobs/history")
    row = next(x for x in r.json() if x["job_type"] == "scan" and x["started_at"] == "2026-02-01T00:00:00")
    assert row["status"] == "interrupted"
    assert row["result"] == ""


def test_jobs_history_respects_limit_query_param(test_client):
    r = test_client.get("/api/jobs/history?limit=1000")
    assert r.status_code == 200
    r2 = test_client.get("/api/jobs/history?limit=0")
    assert r2.status_code == 422  # ge=1 constraint


def test_version_info_is_public_and_returns_version_string(test_client):
    r = test_client.get("/api/version")
    assert r.status_code == 200
    assert isinstance(r.json()["version"], str) and r.json()["version"]
