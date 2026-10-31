"""P4 — 'error' status is replayable with backoff (migration m018, both dialects)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
import backend.db.schema as _db_schema
import backend.deletion as _deletion_mod


@pytest.fixture
async def dbpath(tmp_path, monkeypatch):
    import backend.db.engine as _db_engine
    path = str(tmp_path / "p4.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", path)
    monkeypatch.setattr(_db_ss, "DB_PATH", path)
    monkeypatch.setattr(_db_schema, "DB_PATH", path)
    monkeypatch.setattr(_deletion_mod, "DB_PATH", path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", path)
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()
    return path


async def _seed(emby_id="e1", status="pending", **cols) -> int:
    from backend.db.engine import get_db
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    async with get_db() as db:
        new_id = await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (emby_id, "T", "Movie", "lib", "Lib", "/f.mkv", past, past, status),
        )
        for k, v in cols.items():
            await db.execute(f"UPDATE media_queue SET {k}=? WHERE id=?", (v, new_id))
        await db.commit()
    return new_id


async def _row(item_id) -> dict:
    from backend.db.engine import get_db
    async with get_db() as db:
        return dict(await db.fetch_one("SELECT * FROM media_queue WHERE id=?", (item_id,)))


def _iso(**kw) -> str:
    return (datetime.now(timezone.utc) + timedelta(**kw)).isoformat()


def _cfg(**kw):
    base = dict(dry_run=False, run_id=1, qbit_action="tag_only", qbit_tag="t",
                alert_on_error=True, mention="", msg_tpl="", alert_threshold=99)
    base.update(kw)
    return _deletion_mod._BatchConfig(**base)


# ── schema / migration ───────────────────────────────────────────────────────

async def test_columns_exist_after_init(dbpath):
    async with aiosqlite.connect(dbpath) as db:
        async with db.execute("PRAGMA table_info(media_queue)") as cur:
            cols = {r[1] for r in await cur.fetchall()}
    assert {"attempts", "last_error", "next_retry_at"} <= cols


async def test_ddl_parity_for_new_columns():
    from backend.db.schema import _TABLES
    from backend.db.schema_mariadb import MARIADB_TABLES
    s = next(d for n, d, *_ in _TABLES if n == "media_queue")
    m = next(d for n, d, *_ in MARIADB_TABLES if n == "media_queue")
    for col in ("attempts", "last_error", "next_retry_at"):
        assert col in s and col in m


async def test_m018_adds_columns_sqlite(tmp_path, monkeypatch):
    import backend.db.engine as _e
    from backend.db.migrations import _m018_add_deletion_retry_columns
    path = str(tmp_path / "m018.db")
    for mod, attr in ((_db_utils, "DB_PATH"), (_db_ss, "DB_PATH"), (_db_schema, "DB_PATH"), (_e, "SQLITE_PATH")):
        monkeypatch.setattr(mod, attr, path)
    async with aiosqlite.connect(path) as db:
        await db.execute("CREATE TABLE media_queue (id INTEGER PRIMARY KEY)")
        await db.execute("INSERT INTO media_queue (id) VALUES (1)")
        await db.commit()
    await _m018_add_deletion_retry_columns()
    await _m018_add_deletion_retry_columns()  # idempotent
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT attempts, last_error, next_retry_at FROM media_queue") as cur:
            assert await cur.fetchone() == (0, None, None)


async def test_m018_mariadb_emits_three_alters(monkeypatch):
    import backend.db.migrations as mig
    from tests.test_cov_dbauth_migrations import _simulate_mariadb
    conn = _simulate_mariadb(monkeypatch, existing_cols={"media_queue": {"id"}})
    await mig._m018_add_deletion_retry_columns()
    sql = " ".join(conn.executed)
    assert "ADD COLUMN attempts INT NOT NULL DEFAULT 0" in sql
    assert "ADD COLUMN last_error TEXT" in sql
    assert "ADD COLUMN next_retry_at VARCHAR(32)" in sql


def test_m018_is_last_migration():
    from backend.db.migrations import _MIGRATIONS
    assert _MIGRATIONS[-1][0] == "m019"


# ── repository ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("attempt,hours", [(1, 1), (2, 4), (3, 12), (4, 24)])
async def test_record_failure_backoff(dbpath, attempt, hours):
    from backend.db.repositories import record_deletion_failure
    item = await _seed(status="deleting", attempts=attempt - 1)
    info = await record_deletion_failure(item, "x" * 900)
    row = await _row(item)
    assert row["status"] == "error" and row["attempts"] == attempt
    assert len(row["last_error"]) == 500
    delta = datetime.fromisoformat(row["next_retry_at"]) - datetime.now(timezone.utc)
    assert timedelta(hours=hours) - timedelta(minutes=1) < delta <= timedelta(hours=hours)
    assert info == {"attempts": attempt, "final": False}


async def test_record_failure_fifth_is_final(dbpath):
    from backend.db.repositories import record_deletion_failure
    item = await _seed(status="deleting", attempts=4)
    info = await record_deletion_failure(item, "boom")
    row = await _row(item)
    assert row["attempts"] == 5 and row["next_retry_at"] is None and row["status"] == "error"
    assert info == {"attempts": 5, "final": True}


async def test_selection_includes_due_errors_only(dbpath):
    from backend.db.repositories import get_pending_queue
    p = await _seed("p", "pending")
    due = await _seed("due", "error", attempts=1, next_retry_at=_iso(minutes=-5))
    await _seed("later", "error", attempts=1, next_retry_at=_iso(hours=2))
    await _seed("final", "error", attempts=5, next_retry_at=None)
    await _seed("capped", "error", attempts=5, next_retry_at=_iso(minutes=-5))
    await _seed("legacy", "error")  # pre-m018 error row: no next_retry_at -> not replayed
    ids = {r["id"] for r in await get_pending_queue()}
    assert ids == {p, due}


async def test_claim_accepts_pending_or_error_not_others(dbpath):
    from backend.db.repositories import claim_for_deletion
    a, b, c, d = (await _seed("a", "pending"), await _seed("b", "error"),
                  await _seed("c", "deleted"), await _seed("d", "deleting"))
    assert await claim_for_deletion(a) and await claim_for_deletion(b)
    assert not await claim_for_deletion(c) and not await claim_for_deletion(d)


async def test_success_resets_retry_state(dbpath):
    from backend.db.repositories import update_queue_status
    item = await _seed(status="deleting", attempts=3, last_error="old", next_retry_at=_iso(hours=1))
    await update_queue_status(item, "deleted")
    row = await _row(item)
    assert (row["status"], row["attempts"], row["last_error"], row["next_retry_at"]) == ("deleted", 0, None, None)


# ── job: alerts only on 1st and final failure ────────────────────────────────

async def _run(item, ok=False):
    with patch.object(_deletion_mod, "_delete_media", new=AsyncMock(return_value=ok)), \
            patch.object(_deletion_mod, "send_alert", new=AsyncMock()) as alert:
        counters = {"errors": 0}
        row = {"id": item, "title": "T", "emby_id": "e"}
        assert await _deletion_mod._claim_pending(item)
        await _deletion_mod._run_claimed(row, _cfg(), counters)
    return alert, counters


async def test_first_failure_alerts_and_records(dbpath):
    item = await _seed()
    alert, counters = await _run(item)
    assert alert.await_count == 1 and counters["errors"] == 1
    assert (await _row(item))["attempts"] == 1


async def test_middle_retry_failure_does_not_alert(dbpath):
    item = await _seed(status="error", attempts=2, next_retry_at=_iso(minutes=-1))
    alert, counters = await _run(item)
    alert.assert_not_awaited()
    assert counters["errors"] == 1  # R3/F4: every failure counts, only the alert is throttled
    assert (await _row(item))["attempts"] == 3


async def test_final_failure_alerts(dbpath):
    item = await _seed(status="error", attempts=4, next_retry_at=_iso(minutes=-1))
    alert, _ = await _run(item)
    assert alert.await_count == 1
    assert (await _row(item))["next_retry_at"] is None


async def test_retry_success_marks_deleted_and_resets(dbpath):
    item = await _seed(status="error", attempts=2, last_error="e", next_retry_at=_iso(minutes=-1))
    await _run(item, ok=True)
    row = await _row(item)
    assert row["status"] == "deleted" and row["attempts"] == 0 and row["next_retry_at"] is None


async def test_exception_in_pipeline_recorded_as_failure_with_message(dbpath):
    item = await _seed()
    with patch.object(_deletion_mod, "_delete_media", new=AsyncMock(side_effect=RuntimeError("kaboom"))), \
            patch.object(_deletion_mod, "send_alert", new=AsyncMock()):
        assert await _deletion_mod._claim_pending(item)
        await _deletion_mod._run_claimed({"id": item, "title": "T"}, _cfg(), {"errors": 0})
    row = await _row(item)
    assert row["status"] == "error" and "kaboom" in row["last_error"]
