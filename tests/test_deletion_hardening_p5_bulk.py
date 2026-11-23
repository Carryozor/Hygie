"""P5 — POST /api/media/bulk action=delete aligned on delete_now."""
from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
async def _setup(test_client):
    import backend.routers.media as media_mod
    from backend.db.schema import init_db
    from backend.db.engine import get_db
    from backend.db.settings_store import _invalidate_settings_cache, set_setting

    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM media_queue")
        await db.execute("DELETE FROM libraries WHERE id='libP5'")
        await db.commit()
    await set_setting("dry_run", "false")
    _invalidate_settings_cache()
    test_client.app.dependency_overrides[media_mod.require_auth] = lambda: "u"
    yield
    test_client.app.dependency_overrides.pop(media_mod.require_auth, None)


async def _seed(emby_id, status="pending", **cols) -> int:
    from backend.db.engine import get_db
    async with get_db() as db:
        new_id = await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (emby_id, f"T-{emby_id}", "Movie", "libP5", "L", "/f.mkv",
             "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00", status),
        )
        for k, v in cols.items():
            await db.execute(f"UPDATE media_queue SET {k}=? WHERE id=?", (v, new_id))
        await db.commit()
    return new_id


async def _status(item_id):
    from backend.db.engine import get_db
    async with get_db() as db:
        r = await db.fetch_one("SELECT status FROM media_queue WHERE id=?", (item_id,))
    return r["status"] if r else None


async def _set_dry(v: bool):
    from backend.db.settings_store import set_setting
    await set_setting("dry_run", "true" if v else "false")


def _patch(monkeypatch, fake):
    import backend.routers.media as m
    monkeypatch.setattr(m, "_delete_media", fake)


async def test_dry_run_changes_no_status(test_client, monkeypatch):
    await _set_dry(True)
    a = await _seed("a")
    fake = AsyncMock(return_value=True)
    _patch(monkeypatch, fake)
    r = test_client.post("/api/media/bulk", json={"ids": [a], "action": "delete"})
    assert r.status_code == 200
    assert await _status(a) == "pending"
    assert fake.await_args.args[1] is True


async def test_already_deleted_row_is_not_reprocessed(test_client, monkeypatch):
    done = await _seed("d", status="deleted")
    pend = await _seed("p")
    fake = AsyncMock(return_value=True)
    _patch(monkeypatch, fake)
    r = test_client.post("/api/media/bulk", json={"ids": [done, pend], "action": "delete"})
    assert r.json() == {"affected": 1, "skipped": 1}
    assert fake.await_count == 1
    assert fake.await_args.args[0]["emby_id"] == "p"


async def test_exception_on_one_row_does_not_stop_the_batch(test_client, monkeypatch):
    a, b, c = await _seed("a"), await _seed("b"), await _seed("c")

    async def _fake(row, dry_run, **_kw):
        if row["emby_id"] == "b":
            raise RuntimeError("boom")
        return True
    _patch(monkeypatch, _fake)
    r = test_client.post("/api/media/bulk", json={"ids": [a, b, c], "action": "delete"})
    assert r.status_code == 200
    assert r.json() == {"affected": 2, "skipped": 1}
    assert await _status(a) == "deleted" and await _status(c) == "deleted"
    assert await _status(b) == "error"  # never left in 'deleting'; R2/B2: recorded as a failed attempt


async def test_watched_since_queued_is_rescued_not_deleted(test_client, monkeypatch):
    a = await _seed("a", last_played="2026-03-01T00:00:00+00:00")  # played after detected_at
    fake = AsyncMock(return_value=True)
    _patch(monkeypatch, fake)
    r = test_client.post("/api/media/bulk", json={"ids": [a], "action": "delete"})
    assert r.json() == {"affected": 0, "skipped": 1}
    fake.assert_not_awaited()
    assert await _status(a) is None  # removed from queue by the rescue guard


async def test_claim_lost_row_is_skipped(test_client, monkeypatch):
    a = await _seed("a")
    import backend.routers.media as m
    monkeypatch.setattr(m, "claim_for_deletion", AsyncMock(return_value=False))
    fake = AsyncMock(return_value=True)
    _patch(monkeypatch, fake)
    r = test_client.post("/api/media/bulk", json={"ids": [a], "action": "delete"})
    assert r.json() == {"affected": 0, "skipped": 1}
    fake.assert_not_awaited()


async def test_server_id_is_resolved_from_library(test_client, monkeypatch):
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO libraries (id, name, emby_library_id, conditions, logic, grace_days, "
            "seerr_conditions, enabled, created_at, server_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("libP5", "L", "x", "[]", "AND", 0, "[]", 1, "2026-01-01T00:00:00+00:00", "srv7"),
        )
        await db.commit()
    a = await _seed("a")
    seen = {}

    async def _fake(row, dry_run, **_kw):
        seen["sid"] = row.get("_server_id")
        return True
    _patch(monkeypatch, _fake)
    test_client.post("/api/media/bulk", json={"ids": [a], "action": "delete"})
    assert seen["sid"] == "srv7"


async def test_delete_now_also_gets_server_id(test_client, monkeypatch):
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO libraries (id, name, emby_library_id, conditions, logic, grace_days, "
            "seerr_conditions, enabled, created_at, server_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("libP5", "L", "x", "[]", "AND", 0, "[]", 1, "2026-01-01T00:00:00+00:00", "srv7"),
        )
        await db.commit()
    a = await _seed("a")
    seen = {}

    async def _fake(row, dry_run, **_kw):
        seen["sid"] = row.get("_server_id")
        return True
    _patch(monkeypatch, _fake)
    assert test_client.post(f"/api/media/{a}/delete-now").status_code == 200
    assert seen["sid"] == "srv7"
