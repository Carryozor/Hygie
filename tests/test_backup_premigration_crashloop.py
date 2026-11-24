"""P2-5 (4.6.0): a boot that crashes in a migration must not evict restore points.

Each retry (and each uvicorn worker) used to dump again as a periodic backup,
so the rotation deleted the older dumps, including the one taken before the
failing migration first ran. Pre-migration dumps are now named
hygie_premig_<first pending id>_<ts>, kept outside the periodic rotation with
their own cap, and never repeated for the same first pending id.
"""
import asyncio
import os
import time
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("DB_PATH", ":memory:")

import backend.backup as backup_mod

pytestmark = pytest.mark.usefixtures("trust_backup_content")

FAKE_DUMP = b"-- fake sqlite copy\n" + b"INSERT INTO t VALUES (1);\n" * 400


@pytest.fixture
def env(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    state = {"pending": ["m021"], "retention": 3}

    async def _settings(key, default=None):
        return {"backup_path": str(backup_dir)}.get(key, default)

    async def _bools(key, default=False):
        return {"backup_enabled": True}.get(key, default)

    async def _ints(key, default=0):
        return {"backup_interval_hours": 24,
                "backup_retention_count": state["retention"]}.get(key, default)

    async def _pending():
        return list(state["pending"])

    async def _exists():
        return True

    monkeypatch.setattr(backup_mod, "DIALECT", "sqlite")
    monkeypatch.setattr(backup_mod, "SQLITE_PATH", "/app/data/hygie.db")
    monkeypatch.setattr(backup_mod, "get_setting", _settings)
    monkeypatch.setattr(backup_mod, "get_bool_setting", _bools)
    monkeypatch.setattr(backup_mod, "get_int_setting", _ints)
    monkeypatch.setattr(backup_mod, "_db_already_exists", _exists)
    monkeypatch.setattr(backup_mod, "add_log", AsyncMock())

    def _fake_copy(src, dst):
        with open(dst, "wb") as f:
            f.write(FAKE_DUMP)

    monkeypatch.setattr(backup_mod, "_do_sqlite_backup", _fake_copy)
    import backend.db.migrations as mig
    monkeypatch.setattr(mig, "pending_migration_ids", _pending)
    return backup_dir, state


def _periodic(d, n=3):
    names = []
    for i in range(n):
        p = d / f"hygie_2026112{i}_211133.sql.gz"
        p.write_bytes(b"x" * 64)
        t = time.time() - (n - i) * 86400
        os.utime(p, (t, t))
        names.append(p.name)
    return names


def _premig(d):
    return sorted(f for f in os.listdir(d) if f.startswith("hygie_premig_"))


async def test_first_premigration_dump_survives_repeated_failed_boots(env):
    d, _ = env
    periodic = _periodic(d)

    for _ in range(4):
        await backup_mod.backup_before_migrations()

    pre = _premig(d)
    assert len(pre) == 1 and pre[0].startswith("hygie_premig_m021_"), pre
    assert sorted(f for f in os.listdir(d) if f not in pre) == periodic


async def test_two_workers_booting_together_dump_once(env):
    d, _ = env
    await asyncio.gather(
        backup_mod.backup_before_migrations(), backup_mod.backup_before_migrations())
    assert len(_premig(d)) == 1


async def test_premigration_dumps_are_capped_at_three_outside_rotation(env):
    d, state = env
    periodic = _periodic(d)
    for i, mid in enumerate(["m021", "m022", "m023", "m024"]):
        state["pending"] = [mid]
        await backup_mod.backup_before_migrations()
        for f in _premig(d):  # strictly increasing mtimes: deterministic eviction
            if f.startswith(f"hygie_premig_{mid}_"):
                t = time.time() + i
                os.utime(d / f, (t, t))

    pre = _premig(d)
    assert len(pre) == 3
    assert not any(f.startswith("hygie_premig_m021_") for f in pre)
    assert all(f in os.listdir(d) for f in periodic)


async def test_a_new_first_pending_id_gets_its_own_dump(env):
    d, state = env
    await backup_mod.backup_before_migrations()
    state["pending"] = ["m022", "m023"]
    await backup_mod.backup_before_migrations()
    assert len(_premig(d)) == 2


async def test_an_empty_existing_dump_does_not_block_a_new_one(env):
    d, _ = env
    (d / "hygie_premig_m021_20261124_100000.sql.gz").write_bytes(b"")
    await backup_mod.backup_before_migrations()
    valid = [f for f in _premig(d) if (d / f).stat().st_size > 0]
    assert len(valid) == 1


async def test_unrestorable_existing_dump_is_replaced(env, monkeypatch):
    d, _ = env
    old = d / "hygie_premig_m021_20261124_100000.db"
    old.write_bytes(b"x" * 64)
    monkeypatch.setattr(
        backup_mod, "verify_backup_content",
        lambda path: {"ok": path != str(old), "format": "t", "reason": "truncated"})
    await backup_mod.backup_before_migrations()
    assert len(_premig(d)) == 2


async def test_periodic_rotation_ignores_premigration_dumps(env):
    d, state = env
    state["retention"] = 2
    await backup_mod.backup_before_migrations()
    pre = _premig(d)
    for _ in range(3):
        assert await backup_mod.run_backup(force=True)
    periodic = [f for f in os.listdir(d) if not f.startswith("hygie_premig_")]
    assert len(periodic) == 2
    assert _premig(d) == pre


async def test_premigration_dump_is_listed_verifiable_and_deletable(env, monkeypatch):
    import backend.routers.backup as router
    d, _ = env
    await backup_mod.backup_before_migrations()
    name = _premig(d)[0]

    async def _setting(key, default=None):
        return str(d) if key == "backup_path" else default

    monkeypatch.setattr(router, "get_setting", _setting)
    monkeypatch.setattr(router, "verify_backup_content",
                        lambda path: {"ok": True, "format": "test", "reason": None})
    assert name in [b["filename"] for b in await router.get_backups(user="u")]
    assert (await router.verify_backup(name, user="u"))["ok"] is True
    assert await router.delete_backup(name, user="u") == {"status": "deleted"}
    assert _premig(d) == []


async def test_failed_premigration_dump_never_blocks_startup(env, monkeypatch):
    def _boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(backup_mod, "_do_sqlite_backup", _boom)
    await backup_mod.backup_before_migrations()  # must not raise


async def test_premigration_backup_skips_when_backups_disabled(env, monkeypatch):
    d, _ = env

    async def _off(key, default=False):
        return False

    monkeypatch.setattr(backup_mod, "get_bool_setting", _off)
    await backup_mod.backup_before_migrations()
    assert _premig(d) == []
