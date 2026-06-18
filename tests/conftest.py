"""Shared fixtures for all tests."""
import os

# Force test encryption key and in-memory DB before any module import
os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")

import pytest
from unittest.mock import MagicMock


@pytest.fixture(autouse=True)
def _reset_circuit_breakers():
    """Circuit breakers live in a process-global registry (arr_clients/circuit_breaker.py).
    Without resetting it, a breaker tripped OPEN by one test's failure
    simulation leaks into any later test that happens to share the same
    breaker name (e.g. "emby:0"), making that later test's behavior depend
    on test execution order instead of its own setup.
    """
    from backend.arr_clients import circuit_breaker
    circuit_breaker._registry.clear()
    yield
    circuit_breaker._registry.clear()


@pytest.fixture(scope="session")
def test_client(tmp_path_factory):
    """Synchronous TestClient for lightweight route tests. Auth dependency is bypassed."""
    import importlib
    from fastapi.testclient import TestClient
    from argon2 import PasswordHasher

    import backend.db.utils as _db_utils
    import backend.db.settings_store as _db_ss
    import backend.db.media_servers as _db_ms
    import backend.db.schema as _db_schema
    import backend.db.logs as _db_logs
    import backend.db.repositories as _db_repos
    import backend.db.engine as _db_engine
    import backend.routers.stats as _r_stats
    import backend.routers.metrics as _r_metrics
    import backend.routers.calendar as _r_calendar
    import backend.routers.expert_rules as _r_expert_rules
    import backend.routers.ignored as _r_ignored
    import backend.routers.libraries as _r_libraries
    import backend.routers.logs as _r_logs
    import backend.routers.media as _r_media
    import backend.routers.seerr_rules as _r_seerr
    import backend.routers.storage as _r_storage
    import backend.routers.unmonitored as _r_unmonitored

    db_path = str(tmp_path_factory.mktemp("guard") / "guard_test.db")
    _all_db_modules = [
        _db_utils, _db_ss, _db_ms, _db_schema, _db_logs, _db_repos,
        _r_stats, _r_metrics, _r_calendar, _r_expert_rules, _r_ignored,
        _r_libraries, _r_logs, _r_media, _r_seerr, _r_storage, _r_unmonitored,
    ]
    for mod in _all_db_modules:
        if hasattr(mod, "DB_PATH"):
            mod.DB_PATH = db_path
    _db_engine.SQLITE_PATH = db_path
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0

    import backend.auth as auth_mod
    import backend.main as main_mod
    import backend.routers.scheduler as _sched_router_mod
    importlib.reload(auth_mod)
    importlib.reload(_sched_router_mod)
    importlib.reload(main_mod)
    auth_mod._ph = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    for mod in _all_db_modules:
        if hasattr(mod, "DB_PATH"):
            mod.DB_PATH = db_path
    _db_engine.SQLITE_PATH = db_path

    # After full reload, all routers reference auth_mod.require_auth — one override suffices
    main_mod.app.dependency_overrides[auth_mod.require_auth] = lambda: "testuser"

    # Mock scheduler to avoid event-loop issues
    from datetime import datetime, timezone

    def _make_job(job_id):
        j = MagicMock()
        j.id = job_id
        j.func = MagicMock()
        j.func.__name__ = job_id
        j.next_run_time = datetime.now(timezone.utc)
        return j

    mock_sched = MagicMock()
    mock_sched.get_jobs.return_value = [_make_job("scan_job"), _make_job("deletion_job")]
    main_mod.scheduler = mock_sched
    import backend.routers.scheduler as _sched_router
    _sched_router.scheduler = mock_sched

    return TestClient(main_mod.app)
