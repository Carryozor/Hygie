# backend/startup_validator.py
"""Startup configuration validation.

Checks for missing, invalid, or dangerous configuration at application startup.
CRITICAL issues are logged and prevent serving (via lifespan), WARN issues are
logged but allow startup. Called once from main.py lifespan after DB init.
"""
import logging
import os
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass
class ValidationIssue:
    level: Literal["CRITICAL", "WARN", "INFO"]
    message: str
    remedy: str = ""

    def __str__(self) -> str:
        s = f"[{self.level}] {self.message}"
        if self.remedy:
            s += f" → {self.remedy}"
        return s


class StartupValidator:
    """Validates Hygie configuration at startup and reports issues via logs.

    db_pool_init_error: if init_db_pool() raised before the validator ran,
    pass the error string here so it surfaces as a structured CRITICAL instead
    of a raw traceback crash.
    """

    def __init__(self, db_pool_init_error: str = "") -> None:
        self._db_pool_init_error = db_pool_init_error

    async def run(self) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        await self._check_worker_count(issues)
        await self._check_encryption(issues)
        await self._check_secret_key(issues)
        await self._check_intervals(issues)
        # If the DB pool already failed to initialize, report it immediately
        # instead of re-running a connectivity check that will also fail.
        if self._db_pool_init_error:
            issues.append(ValidationIssue(
                "CRITICAL",
                f"MariaDB pool initialization failed: {self._db_pool_init_error}",
                "Check DATABASE_URL format, host reachability, credentials, and that the "
                "MariaDB server is running. Common causes: bad password, wrong host/port, "
                "IPv6 address in URL, special characters in password.",
            ))
        else:
            await self._check_db_connectivity(issues)
        await self._check_mariadb_defaults(issues)
        return issues

    # ── Individual checks ─────────────────────────────────────────────────────

    async def _check_worker_count(self, issues: list) -> None:
        workers_env = os.environ.get("WORKERS", "1")
        try:
            workers = int(workers_env)
        except (ValueError, TypeError):
            return
        if workers <= 1:
            return

        # Multi-worker mode requires MariaDB (SQLite is not safe for concurrent writers)
        # and the MariaDB advisory lock backend (cross-process job serialization).
        db_url = os.environ.get("DATABASE_URL", "")
        lock_backend = os.environ.get("HYGIE_LOCK_BACKEND", "asyncio").lower()

        if not db_url or "mysql" not in db_url:
            issues.append(ValidationIssue(
                "CRITICAL",
                f"WORKERS={workers} requires MariaDB (DATABASE_URL=mysql+aiomysql://...). "
                "SQLite does not support safe concurrent writes from multiple processes.",
                "Set DATABASE_URL to a MariaDB connection string, or keep WORKERS=1.",
            ))
        if lock_backend != "mariadb":
            issues.append(ValidationIssue(
                "CRITICAL",
                f"WORKERS={workers} requires HYGIE_LOCK_BACKEND=mariadb. "
                "Without it, scan and deletion jobs will run simultaneously in every worker, "
                "causing duplicate queue entries and data corruption.",
                "Set HYGIE_LOCK_BACKEND=mariadb in your environment.",
            ))

    async def _check_mariadb_defaults(self, issues: list) -> None:
        """Warn if DATABASE_URL contains well-known default passwords."""
        db_url = os.environ.get("DATABASE_URL", "")
        for known_default in ("hygie_secret", "root_secret"):
            if known_default in db_url:
                issues.append(ValidationIssue(
                    "WARN",
                    f"DATABASE_URL contains known default password '{known_default}'. "
                    "This is a security risk — the credentials are publicly known.",
                    "Set DB_MARIADB_PASSWORD to a strong random password in your .env file.",
                ))
                break

    async def _check_encryption(self, issues: list) -> None:
        if not os.environ.get("HYGIE_ENCRYPTION_KEY"):
            issues.append(ValidationIssue(
                "WARN",
                "HYGIE_ENCRYPTION_KEY not set — API keys and webhook tokens stored in plaintext.",
                "Generate a key with: "
                "python3 -c \"import secrets,base64; "
                "print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())\" "
                "and set it as HYGIE_ENCRYPTION_KEY.",
            ))

    async def _check_secret_key(self, issues: list) -> None:
        env_key = os.environ.get("SECRET_KEY", "")
        if not env_key:
            issues.append(ValidationIssue(
                "INFO",
                "SECRET_KEY not set — JWT signing key auto-generated and stored in data/.secret. "
                "All active sessions will be invalidated on container restart.",
                "Set SECRET_KEY env var to a stable 48+ char secret to preserve sessions across restarts.",
            ))

    async def _check_intervals(self, issues: list) -> None:
        try:
            from .db.settings_store import get_int_setting
            scan_min = await get_int_setting("scan_interval_minutes", 360)
            del_min  = await get_int_setting("deletion_check_interval_minutes", 60)

            if 0 < scan_min < 5:
                issues.append(ValidationIssue(
                    "WARN",
                    f"scan_interval_minutes={scan_min} is very low. "
                    "Scanning more often than every 5 minutes generates excessive load on "
                    "Emby/Plex and all downstream services (Radarr, Sonarr, Seerr).",
                    "Set scan_interval_minutes ≥ 60 for production use.",
                ))
            if 0 < del_min < 5:
                issues.append(ValidationIssue(
                    "WARN",
                    f"deletion_check_interval_minutes={del_min} is very low.",
                    "Set deletion_check_interval_minutes ≥ 30.",
                ))
        except Exception as e:
            logger.debug("StartupValidator._check_intervals: %s", e)

    async def _check_db_connectivity(self, issues: list) -> None:
        try:
            from .db.engine import get_db
            async with get_db() as db:
                await db.fetch_one("SELECT 1 AS ok")
        except Exception as e:
            issues.append(ValidationIssue(
                "CRITICAL",
                f"Database connectivity check failed: {e}",
                "Check DB_PATH (SQLite) or DATABASE_URL (MariaDB) environment variables.",
            ))

    # ── Reporting ─────────────────────────────────────────────────────────────

    async def log_results(self, issues: list[ValidationIssue]) -> bool:
        """Log all issues. Returns True if startup should proceed (no CRITICAL issues).

        add_log is wrapped per-issue: if the DB is itself unreachable (a CRITICAL issue
        the validator just detected), add_log will also fail — we must not let that
        failure suppress the Python-level warning.
        """
        from .db.logs import add_log

        level_to_log = {"CRITICAL": "ERROR", "WARN": "WARN", "INFO": "INFO"}
        for issue in issues:
            log_level = level_to_log.get(issue.level, "INFO")
            try:
                await add_log(log_level, str(issue), "startup")
            except Exception as _db_err:
                logger.debug("startup_validator: add_log failed (%s) — DB may be unavailable", _db_err)
            log_fn = (
                logger.error   if log_level == "ERROR" else
                logger.warning if log_level == "WARN"  else
                logger.info
            )
            log_fn(str(issue))

        critical = [i for i in issues if i.level == "CRITICAL"]
        if critical:
            logger.error(
                "Hygie startup blocked by %d CRITICAL issue(s). "
                "Fix the issues above before restarting.",
                len(critical),
            )
        return len(critical) == 0
