"""Coverage tests for backend/main.py — lifespan helpers and the SPA fallback route.

backend.main is already imported (and reloaded) by other fixtures in this
session, so tests here import the live `backend.main` module and call its
helper functions directly with an isolated SQLite DB (monkeypatched
DB_PATH/SQLITE_PATH) rather than rebuilding the whole app — these helpers
take no FastAPI dependencies, only module state.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import backend.main as main_mod

WT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
async def _fresh_db(monkeypatch, tmp_path):
    import backend.db.utils as _db_utils
    import backend.db.engine as _eng

    db_path = str(tmp_path / "main_cov.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_eng, "SQLITE_PATH", db_path)
    from backend.db.schema import init_db
    await init_db()
    yield db_path


# ─── _internal_cleanup — each sub-job's failure must not block the others ──

async def test_internal_cleanup_runs_all_three_jobs_on_success():
    with patch.object(main_mod, "run_ignored_cleanup", new=AsyncMock()) as m1, \
         patch.object(main_mod, "sync_emby_collection", new=AsyncMock()) as m2, \
         patch.object(main_mod, "sync_plex_overlays", new=AsyncMock()) as m3:
        await main_mod._internal_cleanup()
    m1.assert_awaited_once()
    m2.assert_awaited_once()
    m3.assert_awaited_once()


async def test_internal_cleanup_continues_after_ignored_cleanup_failure():
    with patch.object(main_mod, "run_ignored_cleanup", new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch.object(main_mod, "sync_emby_collection", new=AsyncMock()) as m2, \
         patch.object(main_mod, "sync_plex_overlays", new=AsyncMock()) as m3:
        await main_mod._internal_cleanup()  # must not raise
    m2.assert_awaited_once()
    m3.assert_awaited_once()


async def test_internal_cleanup_continues_after_collection_sync_failure():
    with patch.object(main_mod, "run_ignored_cleanup", new=AsyncMock()), \
         patch.object(main_mod, "sync_emby_collection", new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch.object(main_mod, "sync_plex_overlays", new=AsyncMock()) as m3:
        await main_mod._internal_cleanup()
    m3.assert_awaited_once()


async def test_internal_cleanup_continues_after_plex_overlay_sync_failure():
    with patch.object(main_mod, "run_ignored_cleanup", new=AsyncMock()) as m1, \
         patch.object(main_mod, "sync_emby_collection", new=AsyncMock()) as m2, \
         patch.object(main_mod, "sync_plex_overlays", new=AsyncMock(side_effect=RuntimeError("boom"))):
        await main_mod._internal_cleanup()  # must not raise
    m1.assert_awaited_once()
    m2.assert_awaited_once()


# ─── _job_next_run ──────────────────────────────────────────────────────────

async def test_job_next_run_defaults_to_30s_when_no_history():
    before = datetime.now(timezone.utc)
    result = await main_mod._job_next_run("scan", 60)
    assert before + timedelta(seconds=25) <= result <= before + timedelta(seconds=35)


async def test_job_next_run_preserves_countdown_from_last_run():
    from backend.db.engine import get_db
    started = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    async with get_db() as db:
        await db.execute(
            "INSERT INTO job_history (job_type, started_at, status) VALUES (?, ?, 'success')",
            ("scan", started),
        )
        await db.commit()
    result = await main_mod._job_next_run("scan", 60)  # last ran 10min ago, interval 60min -> ~50min left
    expected = datetime.now(timezone.utc) + timedelta(minutes=50)
    assert abs((result - expected).total_seconds()) < 10


async def test_job_next_run_falls_back_to_30s_when_overdue():
    from backend.db.engine import get_db
    started = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    async with get_db() as db:
        await db.execute(
            "INSERT INTO job_history (job_type, started_at, status) VALUES (?, ?, 'success')",
            ("scan", started),
        )
        await db.commit()
    before = datetime.now(timezone.utc)
    result = await main_mod._job_next_run("scan", 60)  # 5h ago + 60min interval is long overdue
    assert before <= result <= before + timedelta(seconds=35)


async def test_job_next_run_handles_naive_started_at_as_utc():
    from backend.db.engine import get_db
    naive = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(tzinfo=None).isoformat()
    async with get_db() as db:
        await db.execute(
            "INSERT INTO job_history (job_type, started_at, status) VALUES (?, ?, 'success')",
            ("scan", naive),
        )
        await db.commit()
    result = await main_mod._job_next_run("scan", 60)
    assert result.tzinfo is not None


async def test_job_next_run_swallows_db_errors_and_returns_30s_default():
    with patch("backend.db.engine.get_db", side_effect=RuntimeError("db down")):
        before = datetime.now(timezone.utc)
        result = await main_mod._job_next_run("scan", 60)
    assert before <= result <= before + timedelta(seconds=35)


# ─── _init_database_and_migrate ────────────────────────────────────────────

async def test_init_database_and_migrate_returns_empty_string_on_success():
    with patch("backend.db.engine.init_db_pool", new=AsyncMock()), \
         patch("backend.backup.backup_before_migrations", new=AsyncMock()), \
         patch("backend.db.migrations.run_migrations", new=AsyncMock()):
        err = await main_mod._init_database_and_migrate()
    assert err == ""


async def test_init_database_and_migrate_captures_pool_init_error_and_skips_migrations():
    with patch("backend.db.engine.init_db_pool", new=AsyncMock(side_effect=RuntimeError("bad DATABASE_URL"))), \
         patch("backend.backup.backup_before_migrations", new=AsyncMock()) as mock_backup:
        err = await main_mod._init_database_and_migrate()
    assert "bad DATABASE_URL" in err
    mock_backup.assert_not_called()


# ─── _configure_log_level ───────────────────────────────────────────────────

async def test_configure_log_level_applies_configured_level():
    from backend.db.settings_store import set_setting
    await set_setting("log_level", "DEBUG")
    await main_mod._configure_log_level()
    import logging
    assert logging.getLogger("hygie").level == logging.DEBUG
    # restore default for other tests in the session
    import logging as _l
    _l.getLogger("hygie").setLevel(_l.INFO)


async def test_configure_log_level_swallows_errors():
    # get_setting is imported by name at the top of main.py (`from .db.settings_store
    # import get_setting, ...`), so main.py holds its own bound reference — patching
    # the source module's attribute would not reach it; the module-level name in
    # backend.main itself must be patched.
    with patch.object(main_mod, "get_setting", new=AsyncMock(side_effect=RuntimeError("db down"))):
        await main_mod._configure_log_level()  # must not raise


# ─── _recover_stale_deletions ───────────────────────────────────────────────

async def test_recover_stale_deletions_calls_reset_stale_deleting():
    with patch("backend.deletion.reset_stale_deleting", new=AsyncMock()) as mock_reset:
        await main_mod._recover_stale_deletions()
    mock_reset.assert_awaited_once()


async def test_recover_stale_deletions_swallows_errors():
    with patch("backend.deletion.reset_stale_deleting", new=AsyncMock(side_effect=RuntimeError("boom"))):
        await main_mod._recover_stale_deletions()  # must not raise


# ─── _run_startup_validation ────────────────────────────────────────────────

async def test_run_startup_validation_passes_through_when_no_critical():
    mock_validator = MagicMock()
    mock_validator.run = AsyncMock(return_value=[])
    mock_validator.log_results = AsyncMock(return_value=True)
    with patch("backend.startup_validator.StartupValidator", return_value=mock_validator):
        await main_mod._run_startup_validation("")  # must not raise / exit


async def test_run_startup_validation_exits_process_on_critical_issue():
    mock_validator = MagicMock()
    mock_validator.run = AsyncMock(return_value=["critical issue"])
    mock_validator.log_results = AsyncMock(return_value=False)
    with patch("backend.startup_validator.StartupValidator", return_value=mock_validator):
        with pytest.raises(SystemExit) as exc:
            await main_mod._run_startup_validation("pool error")
    assert exc.value.code == 1


# ─── _schedule_recurring_jobs ───────────────────────────────────────────────

async def test_schedule_recurring_jobs_registers_scan_and_deletion_jobs():
    mock_sched = MagicMock()
    with patch.object(main_mod, "scheduler", mock_sched), \
         patch.object(main_mod, "get_bool_setting", new=AsyncMock(return_value=False)):
        scan_min, del_min = await main_mod._schedule_recurring_jobs()
    assert scan_min == 360
    assert del_min == 60
    job_ids = {c.kwargs.get("id") for c in mock_sched.add_job.call_args_list}
    assert {"scan_job", "deletion_job", "internal_cleanup"} <= job_ids


async def test_schedule_recurring_jobs_falls_back_to_defaults_on_invalid_setting():
    from backend.db.settings_store import set_setting
    await set_setting("scan_interval_minutes", "not-a-number")
    mock_sched = MagicMock()
    with patch.object(main_mod, "scheduler", mock_sched), \
         patch.object(main_mod, "get_bool_setting", new=AsyncMock(return_value=False)):
        scan_min, del_min = await main_mod._schedule_recurring_jobs()
    assert scan_min == 360
    assert del_min == 60


async def test_schedule_recurring_jobs_adds_backup_job_when_enabled():
    mock_sched = MagicMock()
    with patch.object(main_mod, "scheduler", mock_sched), \
         patch.object(main_mod, "get_bool_setting", new=AsyncMock(return_value=True)), \
         patch.object(main_mod, "get_int_setting", new=AsyncMock(return_value=24)):
        await main_mod._schedule_recurring_jobs()
    job_ids = {c.kwargs.get("id") for c in mock_sched.add_job.call_args_list}
    assert "backup_job" in job_ids


async def test_schedule_recurring_jobs_swallows_backup_setup_errors():
    mock_sched = MagicMock()
    # Same module-level-name-binding issue as get_setting above.
    with patch.object(main_mod, "scheduler", mock_sched), \
         patch.object(main_mod, "get_bool_setting", new=AsyncMock(side_effect=RuntimeError("db down"))):
        scan_min, del_min = await main_mod._schedule_recurring_jobs()  # must not raise
    assert scan_min == 360


# ─── _start_storage_prewarm ─────────────────────────────────────────────────

async def test_start_storage_prewarm_returns_task_on_success():
    async def _noop():
        return {}
    with patch("backend.routers.storage._fetch_storage_data", new=_noop):
        task = main_mod._start_storage_prewarm()
    assert isinstance(task, asyncio.Task)
    await task


def test_start_storage_prewarm_returns_none_on_import_failure():
    with patch.dict("sys.modules", {"backend.routers.storage": None}):
        result = main_mod._start_storage_prewarm()
    assert result is None


# ─── _log_startup_complete ──────────────────────────────────────────────────

async def test_log_startup_complete_warns_when_no_encryption_key(monkeypatch, caplog):
    import logging
    monkeypatch.delenv("HYGIE_ENCRYPTION_KEY", raising=False)
    with patch.object(main_mod, "add_log", new=AsyncMock()) as mock_log:
        with caplog.at_level(logging.WARNING, logger="hygie"):
            await main_mod._log_startup_complete(360, 60)
    assert any("HYGIE_ENCRYPTION_KEY not set" in r.message for r in caplog.records)
    mock_log.assert_awaited_once()


async def test_log_startup_complete_silent_when_encryption_key_set(monkeypatch, caplog):
    import logging
    monkeypatch.setenv("HYGIE_ENCRYPTION_KEY", "k")
    with patch.object(main_mod, "add_log", new=AsyncMock()):
        with caplog.at_level(logging.WARNING, logger="hygie"):
            await main_mod._log_startup_complete(360, 60)
    assert not any("HYGIE_ENCRYPTION_KEY not set" in r.message for r in caplog.records)


# ─── _shutdown_lifespan ─────────────────────────────────────────────────────

async def test_shutdown_lifespan_cancels_pending_prewarm_task():
    async def _never_finishes():
        await asyncio.sleep(3600)
    task = asyncio.create_task(_never_finishes())
    mock_sched = MagicMock()
    with patch.object(main_mod, "scheduler", mock_sched), \
         patch("backend.routers.storage.cancel_storage_refresh", new=AsyncMock()), \
         patch("backend.db.engine.close_db_pool", new=AsyncMock()):
        await main_mod._shutdown_lifespan(task)
    assert task.done()
    assert task.cancelled()


async def test_shutdown_lifespan_skips_cancel_for_already_done_prewarm_task():
    async def _done():
        return None
    task = asyncio.create_task(_done())
    await task
    mock_sched = MagicMock()
    with patch.object(main_mod, "scheduler", mock_sched), \
         patch("backend.routers.storage.cancel_storage_refresh", new=AsyncMock()), \
         patch("backend.db.engine.close_db_pool", new=AsyncMock()):
        await main_mod._shutdown_lifespan(task)  # must not raise


async def test_shutdown_lifespan_falls_back_to_wait_false_when_wait_true_raises():
    mock_sched = MagicMock()
    mock_sched.shutdown.side_effect = [RuntimeError("scheduler already stopped"), None]
    with patch.object(main_mod, "scheduler", mock_sched), \
         patch("backend.routers.storage.cancel_storage_refresh", new=AsyncMock()), \
         patch("backend.db.engine.close_db_pool", new=AsyncMock()):
        await main_mod._shutdown_lifespan(None)
    assert mock_sched.shutdown.call_count == 2
    mock_sched.shutdown.assert_any_call(wait=True)
    mock_sched.shutdown.assert_any_call(wait=False)


# ─── /ws — removed with the legacy frontend ────────────────────────────────

def test_ws_endpoint_no_longer_exists():
    """The legacy log-stream WebSocket is gone: no websocket route is registered
    and a connection attempt is refused instead of accepted."""
    from starlette.routing import WebSocketRoute
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    assert not any(isinstance(r, WebSocketRoute) for r in main_mod.app.routes)
    assert not hasattr(main_mod, "websocket_endpoint")
    # The GET catch-all is an HTTP route, so ASGI refuses the websocket scope.
    with TestClient(main_mod.app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws"):
                pass


# ─── SPA fallback ────────────────────────────────────────────────────────────

def _spa_only_app():
    from fastapi import FastAPI
    app = FastAPI()
    app.add_api_route("/{full_path:path}", main_mod.spa_fallback)
    return app


def test_spa_fallback_returns_404_for_unmatched_api_route():
    from starlette.testclient import TestClient
    with TestClient(_spa_only_app()) as client:
        r = client.get("/api/this-route-does-not-exist")
    assert r.status_code == 404
    assert "API route not found" in r.text


def test_spa_fallback_serves_dist_index_when_present(tmp_path):
    from starlette.testclient import TestClient
    fake_dist = tmp_path / "dist"
    fake_dist.mkdir()
    (fake_dist / "index.html").write_text("<html>dist build</html>")
    with patch.object(main_mod, "_DIST", str(fake_dist)):
        with TestClient(_spa_only_app()) as client:
            r = client.get("/some/spa/route")
    assert r.status_code == 200
    assert "dist build" in r.text
    assert r.headers.get("cache-control") == "no-store"


def test_spa_fallback_returns_503_when_dist_missing(tmp_path):
    from starlette.testclient import TestClient
    empty_dist = tmp_path / "no-dist-here"
    with patch.object(main_mod, "_DIST", str(empty_dist)):
        with TestClient(_spa_only_app()) as client:
            r = client.get("/some/spa/route")
    assert r.status_code == 503
    assert "Frontend non construit" in r.text
    assert r.headers.get("cache-control") == "no-store"


# ─── CORS wildcard rejection (module import-time guard) ────────────────────

def test_wildcard_cors_origin_aborts_at_import(tmp_path):
    """HYGIE_ALLOWED_ORIGINS=* must refuse to start (incompatible with
    allow_credentials=True per the CORS spec) — exercised via a real
    subprocess since the check runs once at module import time, which a
    reload() of an already-imported module cannot re-trigger reliably."""
    import subprocess
    import sys
    import os as _os

    env = dict(_os.environ)
    env["HYGIE_ALLOWED_ORIGINS"] = "*"
    env["DB_PATH"] = str(tmp_path / "wildcard_test.db")
    env["HYGIE_ENCRYPTION_KEY"] = "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q="
    proc = subprocess.run(
        [sys.executable, "-c", "import backend.main"],
        cwd=str(WT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 1
    assert "HYGIE_ALLOWED_ORIGINS" in proc.stderr
