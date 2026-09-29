"""Coverage additions for backend/routers/media.py — baseline (with
test_routes.py + test_regenerate_posters_parallel.py + test_enrich_seerr_batching.py)
still missing: stats, list_queue filtering/sorting, delete_now (dry-run and
real), bulk ignore/delete, ignore_one, purge_deleted, remove_from_queue.

These endpoints are the actual deletion surface of the app — delete_now and
bulk("delete") call _delete_media() and flip status to 'deleted'. Every
mutating test asserts DB state after the call, not just the HTTP status,
and checks that only the targeted row(s) were affected.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
async def _bypass_media_router_auth(test_client):
    import backend.routers.media as media_router_mod
    from backend.db.schema import init_db
    from backend.db.engine import get_db

    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM media_queue")
        await db.execute("DELETE FROM ignored_media")
        await db.execute("DELETE FROM settings WHERE `key`='dry_run'")
        await db.commit()
    from backend.db.settings_store import _invalidate_settings_cache
    _invalidate_settings_cache()
    test_client.app.dependency_overrides[media_router_mod.require_auth] = lambda: "testuser"
    yield
    test_client.app.dependency_overrides.pop(media_router_mod.require_auth, None)


async def _seed_item(emby_id: str, **overrides) -> int:
    from backend.db.engine import get_db
    row = {
        "emby_id": emby_id, "title": overrides.pop("title", f"Title-{emby_id}"),
        "media_type": "Movie", "library_id": "lib1", "library_name": "Library",
        "file_path": "/f/x.mkv", "detected_at": "2026-01-01T00:00:00+00:00",
        "delete_at": "2026-01-08T00:00:00+00:00", "status": "pending",
        "seerr_username": None,
    }
    row.update(overrides)
    async with get_db() as db:
        new_id = await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status, seerr_username) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (row["emby_id"], row["title"], row["media_type"], row["library_id"],
             row["library_name"], row["file_path"], row["detected_at"], row["delete_at"],
             row["status"], row["seerr_username"]),
        )
        await db.commit()
        return new_id


async def _fetch_item(item_id: int):
    from backend.db.engine import get_db
    async with get_db() as db:
        return await db.fetch_one("SELECT * FROM media_queue WHERE id=?", (item_id,))


async def _set_dry_run(value: bool) -> None:
    from backend.db.settings_store import set_setting
    await set_setting("dry_run", "true" if value else "false")


# ─── /stats ─────────────────────────────────────────────────────────────────

async def test_stats_counts_by_status(test_client):
    await _seed_item("s1", status="pending")
    await _seed_item("s2", status="pending")
    await _seed_item("s3", status="deleted")
    await _seed_item("s4", status="error")

    r = test_client.get("/api/media/stats")
    assert r.status_code == 200
    assert r.json() == {"pending": 2, "deleted": 1, "error": 1, "total": 4}


# ─── list_queue ─────────────────────────────────────────────────────────────

async def test_list_queue_rejects_invalid_status(test_client):
    r = test_client.get("/api/media?status=bogus")
    assert r.status_code == 422


async def test_list_queue_returns_total_and_items(test_client):
    await _seed_item("q1")
    await _seed_item("q2")
    r = test_client.get("/api/media")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


async def test_list_queue_filters_by_status(test_client):
    await _seed_item("q1", status="pending")
    await _seed_item("q2", status="deleted")
    r = test_client.get("/api/media?status=deleted")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["emby_id"] == "q2"


async def test_list_queue_filters_by_library_id(test_client):
    await _seed_item("q1", library_id="libA")
    await _seed_item("q2", library_id="libB")
    r = test_client.get("/api/media?library_id=libB")
    body = r.json()
    assert [i["emby_id"] for i in body["items"]] == ["q2"]


async def test_list_queue_search_matches_title_library_and_seerr_username(test_client):
    await _seed_item("q1", title="The Matrix")
    await _seed_item("q2", title="Other", library_name="Sci-Fi Library")
    await _seed_item("q3", title="Other2", seerr_username="neo")
    await _seed_item("q4", title="Unrelated")

    r = test_client.get("/api/media?search=matrix")
    assert [i["emby_id"] for i in r.json()["items"]] == ["q1"]

    r2 = test_client.get("/api/media?search=sci-fi")
    assert [i["emby_id"] for i in r2.json()["items"]] == ["q2"]

    r3 = test_client.get("/api/media?search=neo")
    assert [i["emby_id"] for i in r3.json()["items"]] == ["q3"]


async def test_list_queue_sort_and_direction(test_client):
    await _seed_item("q1", title="Bravo")
    await _seed_item("q2", title="Alpha")
    r = test_client.get("/api/media?sort=title&dir=asc")
    assert [i["title"] for i in r.json()["items"]] == ["Alpha", "Bravo"]

    r2 = test_client.get("/api/media?sort=title&dir=desc")
    assert [i["title"] for i in r2.json()["items"]] == ["Bravo", "Alpha"]


async def test_list_queue_unknown_sort_field_falls_back_to_delete_at(test_client):
    """sort is looked up in the _SORT_MAP allowlist; an unmapped value must
    not error (and must not be interpolated raw into SQL) — it silently
    falls back to the default column."""
    await _seed_item("q1")
    r = test_client.get("/api/media?sort=not_a_real_column;DROP TABLE media_queue")
    assert r.status_code == 200
    assert r.json()["total"] == 1


async def test_list_queue_respects_limit_and_offset(test_client):
    for i in range(5):
        await _seed_item(f"q{i}", title=f"T{i}")
    r = test_client.get("/api/media?limit=2&offset=1&sort=title")
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["items"][0]["title"] == "T1"


# ─── delete_now ─────────────────────────────────────────────────────────────

async def test_delete_now_dry_run_404_when_missing(test_client):
    await _set_dry_run(True)
    r = test_client.post("/api/media/999999/delete-now")
    assert r.status_code == 404


async def test_delete_now_dry_run_404_when_not_pending(test_client):
    await _set_dry_run(True)
    item_id = await _seed_item("d1", status="deleted")
    r = test_client.post(f"/api/media/{item_id}/delete-now")
    assert r.status_code == 404


async def test_delete_now_dry_run_does_not_change_status(test_client, monkeypatch):
    await _set_dry_run(True)
    item_id = await _seed_item("d2", status="pending")
    fake_delete = AsyncMock(return_value=True)
    import backend.routers.media as media_mod
    monkeypatch.setattr(media_mod, "_delete_media", fake_delete)

    r = test_client.post(f"/api/media/{item_id}/delete-now")
    assert r.status_code == 200
    assert r.json() == {"status": "dry_run"}
    fake_delete.assert_awaited_once()
    assert fake_delete.await_args.args[1] is True  # dry_run=True passed through

    row = await _fetch_item(item_id)
    assert row["status"] == "pending"  # untouched — dry run must not mutate state


async def test_delete_now_real_success_marks_deleted(test_client, monkeypatch):
    await _set_dry_run(False)
    item_id = await _seed_item("d3", status="pending")
    import backend.routers.media as media_mod
    monkeypatch.setattr(media_mod, "_delete_media", AsyncMock(return_value=True))

    r = test_client.post(f"/api/media/{item_id}/delete-now")
    assert r.status_code == 200
    assert r.json() == {"status": "deleted"}

    row = await _fetch_item(item_id)
    assert row["status"] == "deleted"


async def test_delete_now_real_failure_reverts_to_pending_and_returns_500(test_client, monkeypatch):
    await _set_dry_run(False)
    item_id = await _seed_item("d4", status="pending")
    import backend.routers.media as media_mod
    monkeypatch.setattr(media_mod, "_delete_media", AsyncMock(return_value=False))

    r = test_client.post(f"/api/media/{item_id}/delete-now")
    assert r.status_code == 500

    row = await _fetch_item(item_id)
    assert row["status"] == "pending"  # claimed as 'deleting' then reverted


async def test_delete_now_404_when_already_claimed(test_client):
    await _set_dry_run(False)
    item_id = await _seed_item("d5", status="deleting")
    r = test_client.post(f"/api/media/{item_id}/delete-now")
    assert r.status_code == 404


async def test_delete_now_only_affects_targeted_item(test_client, monkeypatch):
    """Deleting one item must not touch a sibling row."""
    await _set_dry_run(False)
    target_id = await _seed_item("d6", status="pending")
    other_id = await _seed_item("d7", status="pending")
    import backend.routers.media as media_mod
    monkeypatch.setattr(media_mod, "_delete_media", AsyncMock(return_value=True))

    r = test_client.post(f"/api/media/{target_id}/delete-now")
    assert r.status_code == 200

    assert (await _fetch_item(target_id))["status"] == "deleted"
    assert (await _fetch_item(other_id))["status"] == "pending"


# ─── bulk ───────────────────────────────────────────────────────────────────

async def test_bulk_invalid_action_returns_400(test_client):
    item_id = await _seed_item("b0")
    r = test_client.post("/api/media/bulk", json={"ids": [item_id], "action": "nope"})
    assert r.status_code == 400


async def test_bulk_ignore_moves_rows_to_ignored_and_removes_from_queue(test_client):
    id1 = await _seed_item("b1", title="Ignore Me")
    id2 = await _seed_item("b2", title="Keep Me")

    r = test_client.post("/api/media/bulk", json={"ids": [id1], "action": "ignore", "reason": "not interested"})
    assert r.status_code == 200
    assert r.json() == {"affected": 1}

    assert await _fetch_item(id1) is None
    assert (await _fetch_item(id2)) is not None

    from backend.db.engine import get_db
    async with get_db() as db:
        ignored = await db.fetch_one("SELECT * FROM ignored_media WHERE emby_id='b1'")
    assert ignored is not None
    assert ignored["reason"] == "not interested"


async def test_bulk_ignore_sets_expire_at_when_expire_days_given(test_client):
    id1 = await _seed_item("b3")
    before = datetime.now(timezone.utc)
    r = test_client.post(
        "/api/media/bulk",
        json={"ids": [id1], "action": "ignore", "expire_days": 30},
    )
    assert r.status_code == 200

    from backend.db.engine import get_db
    async with get_db() as db:
        ignored = await db.fetch_one("SELECT * FROM ignored_media WHERE emby_id='b3'")
    assert ignored["expire_at"] is not None
    expire_at = datetime.fromisoformat(ignored["expire_at"])
    assert (expire_at - before).days >= 29


async def test_bulk_delete_marks_only_successful_items_deleted(test_client, monkeypatch):
    """A partial failure in bulk-delete must not mark the failed item
    'deleted' — only rows _delete_media() actually succeeded on."""
    id_ok = await _seed_item("b4", status="pending")
    id_fail = await _seed_item("b5", status="pending")
    await _set_dry_run(False)

    async def _fake_delete(row, dry_run):
        return row["emby_id"] == "b4"

    import backend.routers.media as media_mod
    monkeypatch.setattr(media_mod, "_delete_media", _fake_delete)

    r = test_client.post("/api/media/bulk", json={"ids": [id_ok, id_fail], "action": "delete"})
    assert r.status_code == 200
    assert r.json() == {"affected": 1}

    assert (await _fetch_item(id_ok))["status"] == "deleted"
    assert (await _fetch_item(id_fail))["status"] == "pending"


# ─── ignore_one ─────────────────────────────────────────────────────────────

async def test_ignore_one_404_when_missing(test_client):
    r = test_client.post("/api/media/999999/ignore")
    assert r.status_code == 404


async def test_ignore_one_moves_row_and_removes_from_queue(test_client):
    item_id = await _seed_item("i1", title="Single Ignore")
    r = test_client.post(f"/api/media/{item_id}/ignore?reason=meh")
    assert r.status_code == 200
    assert r.json() == {"status": "ignored"}

    assert await _fetch_item(item_id) is None
    from backend.db.engine import get_db
    async with get_db() as db:
        ignored = await db.fetch_one("SELECT * FROM ignored_media WHERE emby_id='i1'")
    assert ignored is not None
    assert ignored["reason"] == "meh"


async def test_ignore_one_sets_expire_at_when_expire_days_given(test_client):
    item_id = await _seed_item("i2")
    before = datetime.now(timezone.utc)
    r = test_client.post(f"/api/media/{item_id}/ignore?expire_days=10")
    assert r.status_code == 200

    from backend.db.engine import get_db
    async with get_db() as db:
        ignored = await db.fetch_one("SELECT * FROM ignored_media WHERE emby_id='i2'")
    assert ignored["expire_at"] is not None
    expire_at = datetime.fromisoformat(ignored["expire_at"])
    assert (expire_at - before).days >= 9


# ─── purge_deleted ──────────────────────────────────────────────────────────

async def test_purge_deleted_removes_only_deleted_status(test_client):
    id_del = await _seed_item("p1", status="deleted")
    id_pending = await _seed_item("p2", status="pending")

    r = test_client.delete("/api/media/purge/deleted")
    assert r.status_code == 200
    assert r.json() == {"purged": 1}

    assert await _fetch_item(id_del) is None
    assert await _fetch_item(id_pending) is not None


# ─── remove_from_queue ──────────────────────────────────────────────────────

async def test_remove_from_queue_deletes_row(test_client):
    item_id = await _seed_item("r1")
    r = test_client.delete(f"/api/media/{item_id}/remove")
    assert r.status_code == 200
    assert r.json() == {"status": "removed"}
    assert await _fetch_item(item_id) is None


async def test_remove_from_queue_bare_route_alias(test_client):
    item_id = await _seed_item("r2")
    r = test_client.delete(f"/api/media/{item_id}")
    assert r.status_code == 200
    assert await _fetch_item(item_id) is None


# ─── enrich_seerr: seerr_external_url branch (not covered by
# test_enrich_seerr_batching.py, which never configures seerr_external_url) ─

async def test_enrich_seerr_builds_request_url_when_external_url_configured(test_client, monkeypatch):
    from unittest.mock import patch
    item_id = await _seed_item("enrich-ext", title="Ext Movie")
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute("UPDATE media_queue SET tmdb_id=?, media_type=? WHERE id=?",
                          ("777", "Movie", item_id))
        await db.commit()

    from backend.db.settings_store import set_setting
    await set_setting("seerr_external_url", "https://seerr.example.com/")

    fake_cache = {"777": {"seerr_id": 1, "user_id": 2, "username": "carol"}}
    with (
        patch("backend.routers.media._get_poster_url", new=AsyncMock(return_value="http://poster/777.jpg")),
        patch("backend.routers.media.build_seerr_request_cache", new=AsyncMock(return_value=fake_cache)),
    ):
        r = test_client.post("/api/media/enrich-seerr")
        assert r.status_code == 200

    async with get_db() as db:
        row = await db.fetch_one(
            "SELECT seerr_request_url, poster_url FROM media_queue WHERE id=?", (item_id,)
        )
    assert row["seerr_request_url"] == "https://seerr.example.com/movie/777"
    assert row["poster_url"] == "http://poster/777.jpg"


# ─── AUTH ───────────────────────────────────────────────────────────────────

def test_list_queue_requires_auth(test_client):
    import backend.routers.media as media_mod
    override = test_client.app.dependency_overrides.pop(media_mod.require_auth, None)
    try:
        r = test_client.get("/api/media")
        assert r.status_code == 401
    finally:
        if override is not None:
            test_client.app.dependency_overrides[media_mod.require_auth] = override
