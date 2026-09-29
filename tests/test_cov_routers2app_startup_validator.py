"""Coverage tests for backend/startup_validator.py — configuration checks run
once at startup. Each _check_* method is tested directly with env vars
monkeypatched, since run() always calls all of them and the branch-specific
env combinations would otherwise interact (e.g. worker-count vs interval
checks both touching `issues`).
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.startup_validator import StartupValidator, ValidationIssue


def test_validation_issue_str_includes_remedy_when_present():
    issue = ValidationIssue("WARN", "something's off", "fix it like this")
    assert str(issue) == "[WARN] something's off → fix it like this"


def test_validation_issue_str_omits_arrow_when_no_remedy():
    issue = ValidationIssue("INFO", "fyi")
    assert str(issue) == "[INFO] fyi"


# ─── _check_worker_count ────────────────────────────────────────────────────

async def test_check_worker_count_ignores_non_numeric_workers_env(monkeypatch):
    monkeypatch.setenv("WORKERS", "not-a-number")
    v = StartupValidator()
    issues = []
    await v._check_worker_count(issues)
    assert issues == []


async def test_check_worker_count_ok_for_single_worker(monkeypatch):
    monkeypatch.setenv("WORKERS", "1")
    v = StartupValidator()
    issues = []
    await v._check_worker_count(issues)
    assert issues == []


async def test_check_worker_count_critical_when_multi_worker_without_mariadb(monkeypatch):
    monkeypatch.setenv("WORKERS", "4")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("HYGIE_LOCK_BACKEND", "mariadb")
    v = StartupValidator()
    issues = []
    await v._check_worker_count(issues)
    assert any("SQLite does not support" in i.message for i in issues)


async def test_check_worker_count_critical_when_lock_backend_not_mariadb(monkeypatch):
    monkeypatch.setenv("WORKERS", "4")
    monkeypatch.setenv("DATABASE_URL", "mysql+aiomysql://u:p@h:3306/hygie")
    monkeypatch.setenv("HYGIE_LOCK_BACKEND", "asyncio")
    v = StartupValidator()
    issues = []
    await v._check_worker_count(issues)
    assert any("HYGIE_LOCK_BACKEND=mariadb" in i.message for i in issues)
    assert not any("SQLite does not support" in i.message for i in issues)


async def test_check_worker_count_clean_when_multiworker_properly_configured(monkeypatch):
    monkeypatch.setenv("WORKERS", "4")
    monkeypatch.setenv("DATABASE_URL", "mysql+aiomysql://u:p@h:3306/hygie")
    monkeypatch.setenv("HYGIE_LOCK_BACKEND", "mariadb")
    v = StartupValidator()
    issues = []
    await v._check_worker_count(issues)
    assert issues == []


# ─── _check_mariadb_defaults ─────────────────────────────────────────────────

async def test_check_mariadb_defaults_warns_on_known_default_password(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql+aiomysql://root:hygie_secret@h:3306/hygie")
    v = StartupValidator()
    issues = []
    await v._check_mariadb_defaults(issues)
    assert len(issues) == 1
    assert issues[0].level == "WARN"


async def test_check_mariadb_defaults_silent_on_strong_password(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql+aiomysql://root:xK9$mP2vQ@h:3306/hygie")
    v = StartupValidator()
    issues = []
    await v._check_mariadb_defaults(issues)
    assert issues == []


# ─── _check_proxy_trust ──────────────────────────────────────────────────────

async def test_check_proxy_trust_silent_when_trust_proxy_set(monkeypatch):
    monkeypatch.setenv("HYGIE_ALLOWED_ORIGINS", "https://hygie.example.com")
    monkeypatch.setenv("HYGIE_TRUST_PROXY", "1")
    v = StartupValidator()
    issues = []
    await v._check_proxy_trust(issues)
    assert issues == []


async def test_check_proxy_trust_warns_on_public_origin_without_trust_flag(monkeypatch):
    monkeypatch.setenv("HYGIE_ALLOWED_ORIGINS", "https://hygie.example.com")
    monkeypatch.setenv("HYGIE_TRUST_PROXY", "0")
    v = StartupValidator()
    issues = []
    await v._check_proxy_trust(issues)
    assert len(issues) == 1
    assert issues[0].level == "WARN"


async def test_check_proxy_trust_silent_for_localhost_only_origins(monkeypatch):
    monkeypatch.setenv("HYGIE_ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:5173")
    monkeypatch.setenv("HYGIE_TRUST_PROXY", "0")
    v = StartupValidator()
    issues = []
    await v._check_proxy_trust(issues)
    assert issues == []


# ─── _check_encryption / _check_secret_key ──────────────────────────────────

async def test_check_encryption_warns_when_key_missing(monkeypatch):
    monkeypatch.delenv("HYGIE_ENCRYPTION_KEY", raising=False)
    v = StartupValidator()
    issues = []
    await v._check_encryption(issues)
    assert len(issues) == 1 and issues[0].level == "WARN"


async def test_check_encryption_silent_when_key_present(monkeypatch):
    monkeypatch.setenv("HYGIE_ENCRYPTION_KEY", "somekey")
    v = StartupValidator()
    issues = []
    await v._check_encryption(issues)
    assert issues == []


async def test_check_secret_key_info_when_missing(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    v = StartupValidator()
    issues = []
    await v._check_secret_key(issues)
    assert len(issues) == 1 and issues[0].level == "INFO"


# ─── _check_intervals ────────────────────────────────────────────────────────

async def test_check_intervals_warns_on_very_low_scan_interval(monkeypatch):
    with patch("backend.db.settings_store.get_int_setting", new=AsyncMock(side_effect=[3, 60])):
        v = StartupValidator()
        issues = []
        await v._check_intervals(issues)
    assert any("scan_interval_minutes" in i.message for i in issues)


async def test_check_intervals_warns_on_very_low_deletion_interval(monkeypatch):
    with patch("backend.db.settings_store.get_int_setting", new=AsyncMock(side_effect=[360, 2])):
        v = StartupValidator()
        issues = []
        await v._check_intervals(issues)
    assert any("deletion_check_interval_minutes" in i.message for i in issues)


async def test_check_intervals_silent_for_healthy_values(monkeypatch):
    with patch("backend.db.settings_store.get_int_setting", new=AsyncMock(side_effect=[360, 60])):
        v = StartupValidator()
        issues = []
        await v._check_intervals(issues)
    assert issues == []


async def test_check_intervals_swallows_exceptions(monkeypatch):
    with patch("backend.db.settings_store.get_int_setting", new=AsyncMock(side_effect=RuntimeError("db down"))):
        v = StartupValidator()
        issues = []
        await v._check_intervals(issues)  # must not raise
    assert issues == []


# ─── _check_db_connectivity ──────────────────────────────────────────────────

async def test_check_db_connectivity_critical_on_failure():
    with patch("backend.db.engine.get_db", side_effect=RuntimeError("connection refused")):
        v = StartupValidator()
        issues = []
        await v._check_db_connectivity(issues)
    assert len(issues) == 1
    assert issues[0].level == "CRITICAL"
    assert "connection refused" in issues[0].message


async def test_check_db_connectivity_silent_on_success(tmp_path, monkeypatch):
    import backend.db.utils as _db_utils
    import backend.db.engine as _eng
    db_path = str(tmp_path / "connectivity_ok.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_eng, "SQLITE_PATH", db_path)
    from backend.db.schema import init_db
    await init_db()

    v = StartupValidator()
    issues = []
    await v._check_db_connectivity(issues)
    assert issues == []


# ─── run() — db_pool_init_error short-circuit ───────────────────────────────

async def test_run_checks_db_connectivity_when_pool_init_succeeded(tmp_path, monkeypatch):
    """When db_pool_init_error is empty, run() must call _check_db_connectivity
    instead of short-circuiting with a pre-baked CRITICAL."""
    import backend.db.utils as _db_utils
    import backend.db.engine as _eng
    db_path = str(tmp_path / "run_success.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_eng, "SQLITE_PATH", db_path)
    monkeypatch.delenv("WORKERS", raising=False)
    monkeypatch.setenv("HYGIE_ENCRYPTION_KEY", "k")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from backend.db.schema import init_db
    await init_db()

    v = StartupValidator(db_pool_init_error="")
    with patch.object(v, "_check_db_connectivity", new=AsyncMock()) as mock_conn_check:
        await v.run()
    mock_conn_check.assert_called_once()



async def test_run_reports_pool_init_error_as_critical_without_reconnecting(monkeypatch):
    monkeypatch.delenv("WORKERS", raising=False)
    v = StartupValidator(db_pool_init_error="bad password")
    with patch.object(v, "_check_db_connectivity", new=AsyncMock()) as mock_conn_check:
        issues = await v.run()
    mock_conn_check.assert_not_called()
    assert any("bad password" in i.message and i.level == "CRITICAL" for i in issues)


# ─── log_results ──────────────────────────────────────────────────────────

async def test_log_results_returns_true_when_no_critical_issues():
    v = StartupValidator()
    with patch("backend.db.logs.add_log", new=AsyncMock()):
        can_start = await v.log_results([ValidationIssue("WARN", "minor")])
    assert can_start is True


async def test_log_results_returns_false_when_critical_issue_present():
    v = StartupValidator()
    with patch("backend.db.logs.add_log", new=AsyncMock()):
        can_start = await v.log_results([ValidationIssue("CRITICAL", "boom")])
    assert can_start is False


async def test_log_results_continues_after_add_log_failure():
    """If the DB is itself unreachable, add_log will fail too — that failure
    must not suppress the CRITICAL verdict."""
    v = StartupValidator()
    with patch("backend.db.logs.add_log", new=AsyncMock(side_effect=RuntimeError("db down"))):
        can_start = await v.log_results([ValidationIssue("CRITICAL", "db unreachable")])
    assert can_start is False
