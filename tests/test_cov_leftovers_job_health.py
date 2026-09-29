"""Coverage gaps in backend/_job_health.py not hit by
tests/test_advisory_lock_connection_affinity.py: the defensive branches
around a missing/garbled last-run row, an unparsable timestamp, a broken
interval setting, and a failing alert channel on an otherwise-starved job.

These matter because warn_if_job_starved() is the only thing standing
between a silently stranded lock (2026-09-18 incident) and a visible alert —
if any of these guards themselves throw, the starvation check breaks instead
of degrading.
"""
from unittest.mock import AsyncMock

import backend._job_health as job_health


async def test_interval_minutes_falls_back_to_default_on_non_numeric_setting(monkeypatch):
    """A corrupted/non-numeric setting value must not crash the interval calc."""
    monkeypatch.setattr(job_health, "get_setting", AsyncMock(return_value="not-a-number"))

    result = await job_health._interval_minutes("scan_interval_minutes", 360)

    assert result == 360


async def test_interval_minutes_falls_back_to_default_when_setting_is_none(monkeypatch):
    monkeypatch.setattr(job_health, "get_setting", AsyncMock(return_value=None))

    result = await job_health._interval_minutes("scan_interval_minutes", 42)

    assert result == 42


async def test_starvation_check_returns_none_when_last_run_lookup_raises(monkeypatch):
    """get_last_job_run() failing (DB error) must not propagate — it should
    degrade to 'unknown health', not crash the scheduler tick that calls it."""
    monkeypatch.setattr(job_health, "get_last_job_run",
                         AsyncMock(side_effect=RuntimeError("db unavailable")))

    result = await job_health.warn_if_job_starved(
        "scan", "scan_interval_minutes", 360, "Scan de la médiathèque")

    assert result is None


async def test_starvation_check_returns_none_when_job_never_ran(monkeypatch):
    """No prior run recorded at all (fresh install) — nothing to be starved
    relative to, so this must not be reported as an incident."""
    monkeypatch.setattr(job_health, "get_last_job_run", AsyncMock(return_value=None))

    result = await job_health.warn_if_job_starved(
        "scan", "scan_interval_minutes", 360, "Scan de la médiathèque")

    assert result is None


async def test_starvation_check_returns_none_when_started_at_is_unparsable(monkeypatch):
    """A garbled started_at value must not crash the health check — treat it
    as 'cannot determine age' rather than raising out of the scheduler."""
    monkeypatch.setattr(job_health, "get_last_job_run",
                         AsyncMock(return_value={"started_at": "not-a-timestamp"}))

    result = await job_health.warn_if_job_starved(
        "scan", "scan_interval_minutes", 360, "Scan de la médiathèque")

    assert result is None


async def test_starved_job_still_reports_age_when_alert_channel_fails(monkeypatch):
    """The Discord alert failing (network down) must not swallow the fact
    that the job is starved — the ERROR log line and the returned age are
    the fallback signal an operator has if the alert never arrives."""
    from datetime import timedelta
    from backend.db.utils import now_utc

    stale = (now_utc() - timedelta(hours=24)).isoformat()
    logs = []

    monkeypatch.setattr(job_health, "get_last_job_run",
                         AsyncMock(return_value={"started_at": stale}))
    monkeypatch.setattr(job_health, "get_setting", AsyncMock(return_value="360"))
    monkeypatch.setattr(job_health, "add_log",
                         AsyncMock(side_effect=lambda lvl, msg, cat: logs.append((lvl, msg))))
    monkeypatch.setattr(job_health, "send_alert",
                         AsyncMock(side_effect=ConnectionError("discord unreachable")))

    age = await job_health.warn_if_job_starved(
        "scan", "scan_interval_minutes", 360, "Scan de la médiathèque")

    assert age is not None and age > 1400
    assert logs and logs[0][0] == "ERROR"
