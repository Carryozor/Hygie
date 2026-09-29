"""Coverage tests for backend/routers/health.py — the public /health endpoint
used by Uptime Kuma / Docker HEALTHCHECK.

`health()` is called directly (not through HTTP) — it takes no request
parameters and only reads module state (`_scheduler`) plus env vars, so a
direct async call exercises exactly the same code FastAPI would run, without
needing to build an app just for a no-dependency endpoint.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

import backend.routers.health as health_mod


@pytest.fixture(autouse=True)
async def _fresh_db(monkeypatch, tmp_path):
    import backend.db.utils as _db_utils
    import backend.db.engine as _eng

    db_path = str(tmp_path / "health_test.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_eng, "SQLITE_PATH", db_path)

    from backend.db.schema import init_db
    await init_db()
    health_mod.set_scheduler(None)
    yield
    health_mod.set_scheduler(None)


def _make_job(job_id, next_run_time="set"):
    j = MagicMock()
    j.id = job_id
    j.next_run_time = "2026-01-01T00:00:00+00:00" if next_run_time == "set" else None
    return j


async def test_health_returns_200_and_healthy_when_everything_ok(monkeypatch):
    monkeypatch.setenv("HYGIE_ENCRYPTION_KEY", "k")
    mock_sched = MagicMock()
    mock_sched.get_jobs.return_value = [_make_job("scan_job"), _make_job("deletion_job")]
    health_mod.set_scheduler(mock_sched)

    resp = await health_mod.health()
    assert resp.status_code == 200
    import json
    body = json.loads(resp.body)
    assert body["status"] == "healthy"
    assert body["database"] == "ok"
    assert body["scheduler"] == "2 jobs"
    assert body["encryption"] == "enabled"


async def test_health_reports_degraded_when_media_queue_table_missing(monkeypatch, tmp_path):
    """A fresh SQLite file with no schema at all — table_exists('media_queue') is False."""
    import backend.db.utils as _db_utils
    import backend.db.engine as _eng
    empty_db = str(tmp_path / "no_schema.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", empty_db)
    monkeypatch.setattr(_eng, "SQLITE_PATH", empty_db)
    # Touch the file but never run init_db() — sqlite_master exists (0 tables), media_queue doesn't.
    import aiosqlite
    async with aiosqlite.connect(empty_db):
        pass

    resp = await health_mod.health()
    import json
    body = json.loads(resp.body)
    assert body["database"] == "missing required tables"
    assert body["status"] == "degraded"
    assert resp.status_code == 503


async def test_health_reports_database_error_on_connection_failure():
    with patch("backend.routers.health.get_db", side_effect=RuntimeError("db unreachable")):
        resp = await health_mod.health()
    import json
    body = json.loads(resp.body)
    assert body["database"] == "error"
    assert body["status"] == "degraded"


async def test_health_reports_mariadb_dialect_query_path_is_exercised():
    """Forces the mariadb information_schema branch. The underlying connection
    is still SQLite in this test env, so that query fails — proving the
    branch ran (not that MariaDB itself works, which needs real infra) and
    that the surrounding try/except degrades cleanly instead of crashing."""
    with patch("backend.routers.health.DIALECT", "mariadb"):
        resp = await health_mod.health()
    import json
    body = json.loads(resp.body)
    assert body["database"] == "error"
    assert body["status"] == "degraded"


async def test_health_scheduler_unavailable_when_none():
    health_mod.set_scheduler(None)
    resp = await health_mod.health()
    import json
    body = json.loads(resp.body)
    assert body["scheduler"] == "unavailable"


async def test_health_scheduler_degraded_when_critical_job_missing():
    mock_sched = MagicMock()
    mock_sched.get_jobs.return_value = [_make_job("scan_job")]  # deletion_job missing entirely
    health_mod.set_scheduler(mock_sched)
    resp = await health_mod.health()
    import json
    body = json.loads(resp.body)
    assert "degraded" in body["scheduler"]
    assert "deletion_job" in body["scheduler"]
    assert body["status"] == "degraded"


async def test_health_scheduler_degraded_when_next_run_time_is_none():
    mock_sched = MagicMock()
    mock_sched.get_jobs.return_value = [_make_job("scan_job"), _make_job("deletion_job", next_run_time=None)]
    health_mod.set_scheduler(mock_sched)
    resp = await health_mod.health()
    import json
    body = json.loads(resp.body)
    assert "deletion_job" in body["scheduler"]


async def test_health_scheduler_error_reports_unavailable():
    mock_sched = MagicMock()
    mock_sched.get_jobs.side_effect = RuntimeError("scheduler crashed")
    health_mod.set_scheduler(mock_sched)
    resp = await health_mod.health()
    import json
    body = json.loads(resp.body)
    assert body["scheduler"] == "unavailable"


async def test_health_disk_low_marks_degraded(monkeypatch):
    monkeypatch.setattr(
        "backend.routers.health.shutil.disk_usage",
        lambda path: (1000, 1000, 10 * 1024 * 1024),  # 10 MB free < 50 MB threshold
    )
    resp = await health_mod.health()
    import json
    body = json.loads(resp.body)
    assert body["disk"] == "low"
    assert body["status"] == "degraded"


async def test_health_disk_check_failure_reports_unavailable(monkeypatch):
    monkeypatch.setattr(
        "backend.routers.health.shutil.disk_usage",
        MagicMock(side_effect=OSError("no such device")),
    )
    resp = await health_mod.health()
    import json
    body = json.loads(resp.body)
    assert body["disk"] == "unavailable"


async def test_health_encryption_disabled_marks_degraded_only_if_still_healthy(monkeypatch):
    monkeypatch.delenv("HYGIE_ENCRYPTION_KEY", raising=False)
    resp = await health_mod.health()
    import json
    body = json.loads(resp.body)
    assert body["encryption"] == "disabled"
    assert body["status"] == "degraded"


async def test_health_encryption_disabled_does_not_downgrade_an_already_degraded_status(monkeypatch):
    """If status is already degraded (e.g. disk low), a missing encryption key
    must not appear to have caused it — but must still be reported."""
    monkeypatch.delenv("HYGIE_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(
        "backend.routers.health.shutil.disk_usage",
        lambda path: (1000, 1000, 10 * 1024 * 1024),
    )
    resp = await health_mod.health()
    import json
    body = json.loads(resp.body)
    assert body["status"] == "degraded"
    assert body["encryption"] == "disabled"


async def test_health_reports_open_circuit_breakers(monkeypatch):
    monkeypatch.setattr(
        "backend.arr_clients.circuit_breaker.all_breaker_states",
        lambda: {"radarr:0": {"state": "open"}, "sonarr:0": {"state": "closed"}},
    )
    resp = await health_mod.health()
    import json
    body = json.loads(resp.body)
    assert body["circuit_breakers"] == {"open": 1, "total": 2}
    assert body["status"] == "degraded"


async def test_health_circuit_breaker_lookup_failure_is_silently_ignored(monkeypatch):
    monkeypatch.setattr(
        "backend.arr_clients.circuit_breaker.all_breaker_states",
        MagicMock(side_effect=RuntimeError("registry corrupted")),
    )
    resp = await health_mod.health()
    import json
    body = json.loads(resp.body)
    assert "circuit_breakers" not in body
    assert body["status"] == "healthy"
