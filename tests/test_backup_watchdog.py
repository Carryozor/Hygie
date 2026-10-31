"""Backup watchdog (4.4.4).

Between 2026-10-04 and 2026-10-31 the backups silently stopped while the logs
kept saying "Backup créé". Nothing compared the *result* (a recent valid file
on disk) with the expectation, so nobody noticed for 27 days. These tests pin
that comparison: stale/missing dump => visible state + one alert per day.
"""
import os
import time
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("DB_PATH", ":memory:")

import backend._job_health as job_health
import backend.backup as backup_mod

HOUR = 3600


@pytest.fixture
def env(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    cfg = {"enabled": True, "interval": 24, "path": str(backup_dir)}

    async def _settings(key, default=None):
        return {"backup_path": cfg["path"]}.get(key, default)

    async def _bools(key, default=False):
        return {"backup_enabled": cfg["enabled"]}.get(key, default)

    async def _ints(key, default=0):
        return {"backup_interval_hours": cfg["interval"],
                "backup_retention_count": 3}.get(key, default)

    monkeypatch.setattr(backup_mod, "get_setting", _settings)
    monkeypatch.setattr(backup_mod, "get_bool_setting", _bools)
    monkeypatch.setattr(backup_mod, "get_int_setting", _ints)
    monkeypatch.setattr(backup_mod, "SQLITE_PATH", "/app/data/hygie.db")
    return backup_dir, cfg


def _dump(directory, name, age_hours, size=64):
    p = directory / name
    p.write_bytes(b"x" * size)
    t = time.time() - age_hours * HOUR
    os.utime(p, (t, t))
    return p


async def test_status_disabled(env):
    _, cfg = env
    cfg["enabled"] = False
    assert (await backup_mod.backup_status())["state"] == "disabled"


async def test_status_never_when_directory_has_no_dump(env):
    st = await backup_mod.backup_status()
    assert st["state"] == "never"
    assert st["last_backup_at"] is None


async def test_status_ok_with_recent_dump(env):
    d, _ = env
    _dump(d, "hygie_20261031_205800.sql.gz", age_hours=3)
    st = await backup_mod.backup_status()
    assert st["state"] == "ok"
    assert 2.5 < st["age_hours"] < 3.5
    assert st["last_backup_at"]


async def test_status_stale_after_two_intervals_plus_grace(env):
    d, _ = env
    _dump(d, "hygie_20261001_205800.sql.gz", age_hours=24 * 2 + 2)
    assert (await backup_mod.backup_status())["state"] == "stale"


async def test_status_one_missed_cycle_is_still_ok(env):
    d, _ = env
    _dump(d, "hygie_20261030_205800.sql.gz", age_hours=24 + 5)
    assert (await backup_mod.backup_status())["state"] == "ok"


async def test_status_ignores_empty_and_part_files(env):
    d, _ = env
    _dump(d, "hygie_20261031_205800.sql.gz", age_hours=1, size=0)
    _dump(d, "hygie_20261031_205800.abcd1234.sql.gz.part", age_hours=1)
    assert (await backup_mod.backup_status())["state"] == "never"


async def test_status_memory_db_is_disabled(env, monkeypatch):
    monkeypatch.setattr(backup_mod, "SQLITE_PATH", ":memory:")
    assert (await backup_mod.backup_status())["state"] == "disabled"


# ── watchdog ────────────────────────────────────────────────────────────────

@pytest.fixture
def alerts(monkeypatch):
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(job_health, "send_alert", send)
    monkeypatch.setattr(job_health, "add_log", AsyncMock())
    store = {}

    async def _get(key):
        return store.get(key, "")

    async def _set(key, value):
        store[key] = value

    monkeypatch.setattr(job_health, "get_setting", _get)
    monkeypatch.setattr(job_health, "set_setting", _set)
    return send, store


async def test_watchdog_alerts_on_stale_backup(env, alerts):
    d, _ = env
    send, _ = alerts
    _dump(d, "hygie_20261001_205800.sql.gz", age_hours=24 * 27)
    assert await job_health.warn_if_backup_stale() is True
    send.assert_awaited_once()
    assert "sauvegarde" in send.await_args.args[0].lower()


async def test_watchdog_alert_is_throttled_to_once_a_day(env, alerts):
    d, _ = env
    send, _ = alerts
    _dump(d, "hygie_20261001_205800.sql.gz", age_hours=24 * 27)
    await job_health.warn_if_backup_stale()
    assert await job_health.warn_if_backup_stale() is False
    assert send.await_count == 1


async def test_watchdog_realerts_after_a_day(env, alerts):
    d, _ = env
    send, store = alerts
    _dump(d, "hygie_20261001_205800.sql.gz", age_hours=24 * 27)
    await job_health.warn_if_backup_stale()
    store["backup_stale_alert_ts"] = str(time.time() - 25 * HOUR)
    assert await job_health.warn_if_backup_stale() is True
    assert send.await_count == 2


async def test_watchdog_silent_when_healthy(env, alerts):
    d, _ = env
    send, _ = alerts
    _dump(d, "hygie_20261031_205800.sql.gz", age_hours=2)
    assert await job_health.warn_if_backup_stale() is False
    send.assert_not_awaited()


async def test_watchdog_silent_when_backups_disabled(env, alerts):
    _, cfg = env
    send, _ = alerts
    cfg["enabled"] = False
    assert await job_health.warn_if_backup_stale() is False
    send.assert_not_awaited()


async def test_watchdog_never_state_does_not_alert(env, alerts):
    # Fresh install: the first dump only appears after the first interval.
    send, _ = alerts
    assert await job_health.warn_if_backup_stale() is False
    send.assert_not_awaited()


async def test_watchdog_survives_alert_channel_failure(env, alerts):
    d, _ = env
    send, _ = alerts
    send.side_effect = RuntimeError("webhook down")
    _dump(d, "hygie_20261001_205800.sql.gz", age_hours=24 * 27)
    assert await job_health.warn_if_backup_stale() is True


# ── endpoint ────────────────────────────────────────────────────────────────

def test_status_endpoint(test_client, monkeypatch):
    import backend.routers.backup as router_mod
    test_client.app.dependency_overrides[router_mod.require_auth] = lambda: "testuser"
    monkeypatch.setattr(
        router_mod, "backup_status",
        AsyncMock(return_value={"state": "stale", "age_hours": 700.0, "interval_hours": 24,
                                "last_backup_at": "2026-10-04T20:58:00+00:00"}),
    )
    try:
        r = test_client.get("/api/backup/status")
    finally:
        test_client.app.dependency_overrides.pop(router_mod.require_auth, None)
    assert r.status_code == 200
    assert r.json()["state"] == "stale"


def test_status_endpoint_requires_auth(test_client):
    assert test_client.get("/api/backup/status").status_code in (401, 403)
