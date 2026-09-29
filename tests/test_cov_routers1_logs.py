"""Coverage additions for backend/routers/logs.py — baseline was 38%
(35/56 statements missing): only import-level references existed."""
import pytest


@pytest.fixture(autouse=True)
async def _bypass_logs_router_auth(test_client):
    import backend.routers.logs as logs_router_mod
    from backend.db.schema import init_db
    from backend.db.engine import get_db

    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM logs")
        await db.commit()
    test_client.app.dependency_overrides[logs_router_mod.require_auth] = lambda: "testuser"
    yield
    test_client.app.dependency_overrides.pop(logs_router_mod.require_auth, None)


async def _seed_log(level="INFO", source="system", message="hello", seen_status=None) -> int:
    from backend.db.engine import get_db
    async with get_db() as db:
        new_id = await db.execute(
            "INSERT INTO logs (ts, level, source, message, seen_status) VALUES (?,?,?,?,?)",
            ("2026-01-01T00:00:00+00:00", level, source, message, seen_status),
        )
        await db.commit()
        return new_id


async def _fetch_log(log_id: int):
    from backend.db.engine import get_db
    async with get_db() as db:
        return await db.fetch_one("SELECT * FROM logs WHERE id=?", (log_id,))


# ─── list_logs ──────────────────────────────────────────────────────────────

async def test_list_logs_empty_initially(test_client):
    r = test_client.get("/api/logs")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_logs_filters_by_level(test_client):
    await _seed_log(level="INFO", message="info msg")
    await _seed_log(level="ERROR", message="error msg")
    r = test_client.get("/api/logs?level=ERROR")
    body = r.json()
    assert len(body) == 1
    assert body[0]["message"] == "error msg"


async def test_list_logs_filters_by_source(test_client):
    await _seed_log(source="scheduler", message="a")
    await _seed_log(source="library", message="b")
    r = test_client.get("/api/logs?source=library")
    body = r.json()
    assert [row["message"] for row in body] == ["b"]


async def test_list_logs_search_matches_message_substring(test_client):
    await _seed_log(message="disk space low")
    await _seed_log(message="unrelated")
    r = test_client.get("/api/logs?search=disk")
    body = r.json()
    assert [row["message"] for row in body] == ["disk space low"]


async def test_list_logs_respects_limit(test_client):
    for i in range(5):
        await _seed_log(message=f"m{i}")
    r = test_client.get("/api/logs?limit=2")
    assert len(r.json()) == 2


async def test_list_logs_rejects_limit_out_of_range(test_client):
    r = test_client.get("/api/logs?limit=0")
    assert r.status_code == 422
    r2 = test_client.get("/api/logs?limit=999999")
    assert r2.status_code == 422


# ─── update_log_status ─────────────────────────────────────────────────────

async def test_update_log_status_sets_seen(test_client):
    log_id = await _seed_log()
    r = test_client.patch(f"/api/logs/{log_id}", json={"seen_status": "seen"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    row = await _fetch_log(log_id)
    assert row["seen_status"] == "seen"


async def test_update_log_status_sets_acked(test_client):
    log_id = await _seed_log()
    r = test_client.patch(f"/api/logs/{log_id}", json={"seen_status": "acked"})
    assert r.status_code == 200
    row = await _fetch_log(log_id)
    assert row["seen_status"] == "acked"


async def test_update_log_status_invalid_value_clears_to_null(test_client):
    """seen_status must be 'seen'/'acked'/None — anything else is treated as
    a clear, not silently stored as garbage."""
    log_id = await _seed_log(seen_status="seen")
    r = test_client.patch(f"/api/logs/{log_id}", json={"seen_status": "bogus"})
    assert r.status_code == 200
    row = await _fetch_log(log_id)
    assert row["seen_status"] is None


async def test_update_log_status_only_affects_targeted_row(test_client):
    log_id = await _seed_log(message="target")
    other_id = await _seed_log(message="other")
    test_client.patch(f"/api/logs/{log_id}", json={"seen_status": "seen"})
    other_row = await _fetch_log(other_id)
    assert other_row["seen_status"] is None


# ─── unseen-errors-count ────────────────────────────────────────────────────

async def test_unseen_errors_count_zero_when_none(test_client):
    r = test_client.get("/api/logs/unseen-errors-count")
    assert r.status_code == 200
    assert r.json() == {"count": 0}


async def test_unseen_errors_count_ignores_seen_and_non_error(test_client):
    await _seed_log(level="ERROR", seen_status=None)
    await _seed_log(level="ERROR", seen_status="seen")
    await _seed_log(level="INFO", seen_status=None)
    r = test_client.get("/api/logs/unseen-errors-count")
    assert r.json() == {"count": 1}


# ─── mark-seen-errors / ack-errors ─────────────────────────────────────────

async def test_mark_seen_all_errors_updates_only_unseen_errors(test_client):
    id_err = await _seed_log(level="ERROR", seen_status=None)
    id_acked = await _seed_log(level="ERROR", seen_status="acked")
    id_info = await _seed_log(level="INFO", seen_status=None)

    r = test_client.post("/api/logs/mark-seen-errors")
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    assert (await _fetch_log(id_err))["seen_status"] == "seen"
    assert (await _fetch_log(id_acked))["seen_status"] == "acked"  # untouched
    assert (await _fetch_log(id_info))["seen_status"] is None  # untouched


async def test_ack_all_errors_updates_only_unseen_errors(test_client):
    id_err = await _seed_log(level="ERROR", seen_status=None)
    id_seen = await _seed_log(level="ERROR", seen_status="seen")

    r = test_client.post("/api/logs/ack-errors")
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    assert (await _fetch_log(id_err))["seen_status"] == "acked"
    assert (await _fetch_log(id_seen))["seen_status"] == "seen"  # untouched


# ─── clear_logs ─────────────────────────────────────────────────────────────

async def test_clear_logs_deletes_everything(test_client):
    await _seed_log()
    await _seed_log()
    r = test_client.delete("/api/logs")
    assert r.status_code == 200
    assert r.json() == {"status": "cleared"}
    r2 = test_client.get("/api/logs")
    assert r2.json() == []


# ─── AUTH ───────────────────────────────────────────────────────────────────

def test_list_logs_requires_auth(test_client):
    import backend.routers.logs as logs_router_mod
    override = test_client.app.dependency_overrides.pop(logs_router_mod.require_auth, None)
    try:
        r = test_client.get("/api/logs")
        assert r.status_code == 401
    finally:
        if override is not None:
            test_client.app.dependency_overrides[logs_router_mod.require_auth] = override
