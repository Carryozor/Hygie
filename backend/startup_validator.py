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
    """Validates Hygie configuration at startup and reports issues via logs."""

    async def run(self) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        await self._check_worker_count(issues)
        await self._check_encryption(issues)
        await self._check_secret_key(issues)
        await self._check_intervals(issues)
        await self._check_db_connectivity(issues)
        return issues

    # ── Individual checks ─────────────────────────────────────────────────────

    async def _check_worker_count(self, issues: list) -> None:
        workers_env = os.environ.get("WORKERS", "1")
        try:
            if int(workers_env) > 1:
                issues.append(ValidationIssue(
                    "CRITICAL",
                    f"WORKERS={workers_env} is unsupported. "
                    "Hygie uses asyncio.Lock() for scan/deletion exclusivity — "
                    "these locks do NOT extend across OS processes. "
                    "Running >1 worker can cause concurrent scans, duplicate queue entries, "
                    "and data corruption.",
                    "Set WORKERS=1 (the default). To scale horizontally, "
                    "deploy separate Hygie instances with separate databases.",
                ))
        except (ValueError, TypeError):
            pass

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
        """Log all issues. Returns True if startup should proceed (no CRITICAL issues)."""
        from .db.logs import add_log

        level_to_log = {"CRITICAL": "ERROR", "WARN": "WARN", "INFO": "INFO"}
        for issue in issues:
            log_level = level_to_log.get(issue.level, "INFO")
            await add_log(log_level, str(issue), "startup")
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
