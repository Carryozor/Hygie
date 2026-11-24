"""P1-3 — a claimed row never stays 'deleting' when the DB hiccups.

delete-now: a failing reload right after the claim releases it and answers 503.
release_claim: bounded retry on transient DB errors, WARN when it gives up.
"""
import logging
from unittest.mock import AsyncMock

import pytest

import backend.db.repositories as repos


@pytest.fixture(autouse=True)
async def _bypass_auth(test_client):
    import backend.routers.media as media_mod
    from backend.db.engine import get_db
    from backend.db.schema import init_db
    from backend.db.settings_store import _invalidate_settings_cache, set_setting

    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM media_queue")
        await db.commit()
    await set_setting("dry_run", "false")
    _invalidate_settings_cache()
    test_client.app.dependency_overrides[media_mod.require_auth] = lambda: "testuser"
    yield
    test_client.app.dependency_overrides.pop(media_mod.require_auth, None)


async def _seed(status: str = "pending") -> int:
    from backend.db.engine import get_db
    async with get_db() as db:
        nid = await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
            ("e1", "T", "Movie", "l", "L", "/f", "2026-01-01", "2026-01-01", status),
        )
        await db.commit()
    return nid


async def _row(nid: int):
    from backend.db.engine import get_db
    async with get_db() as db:
        return await db.fetch_one("SELECT status, attempts FROM media_queue WHERE id=?", (nid,))


async def test_delete_now_releases_claim_when_reload_fails(test_client, monkeypatch):
    import backend.routers.media as media_mod
    nid = await _seed()
    monkeypatch.setattr(media_mod, "get_by_id", AsyncMock(side_effect=RuntimeError("db down")))
    monkeypatch.setattr(media_mod, "_delete_media", AsyncMock(return_value=True))

    r = test_client.post(f"/api/media/{nid}/delete-now")

    assert r.status_code == 503
    assert r.json()["detail"] == "Base de données indisponible, réessayez"
    assert (await _row(nid))["status"] == "pending"
    media_mod._delete_media.assert_not_awaited()


async def test_delete_now_releases_claim_when_server_map_fails(test_client, monkeypatch):
    import backend.routers.media as media_mod
    nid = await _seed()
    monkeypatch.setattr(media_mod, "_load_library_server_map", AsyncMock(side_effect=RuntimeError("db down")))

    r = test_client.post(f"/api/media/{nid}/delete-now")

    assert r.status_code == 503
    assert (await _row(nid))["status"] == "pending"


async def test_delete_now_still_503_when_release_also_fails(test_client, monkeypatch, caplog):
    import backend.routers.media as media_mod
    nid = await _seed()
    monkeypatch.setattr(media_mod, "get_by_id", AsyncMock(side_effect=RuntimeError("db down")))
    monkeypatch.setattr(media_mod, "_release_claim_once", AsyncMock(side_effect=RuntimeError("still down")))

    with caplog.at_level(logging.ERROR):
        r = test_client.post(f"/api/media/{nid}/delete-now")

    assert r.status_code == 503
    assert "Could not release claim" in caplog.text


async def test_delete_now_404_when_row_vanished_after_claim(test_client, monkeypatch):
    import backend.routers.media as media_mod
    nid = await _seed()
    monkeypatch.setattr(media_mod, "get_by_id", AsyncMock(return_value=None))

    r = test_client.post(f"/api/media/{nid}/delete-now")

    assert r.status_code == 404


def _flaky_get_db(real, failures: int):
    state = {"left": failures, "calls": 0}

    def factory():
        state["calls"] += 1
        if state["left"] > 0:
            state["left"] -= 1
            raise OSError("db unavailable")
        return real()

    return factory, state


async def test_release_claim_retries_transient_db_error(monkeypatch):
    nid = await _seed("deleting")
    waits: list[float] = []
    monkeypatch.setattr(repos, "_release_sleep", AsyncMock(side_effect=waits.append))
    flaky, state = _flaky_get_db(repos.get_db, 2)
    monkeypatch.setattr(repos, "get_db", flaky)

    assert await repos.release_claim(nid) is True

    assert waits == [2, 4]
    assert state["calls"] == 3
    assert (await _row(nid))["status"] == "pending"


async def test_release_claim_gives_up_after_bounded_retries_and_warns(monkeypatch, caplog):
    nid = await _seed("deleting")
    waits: list[float] = []
    monkeypatch.setattr(repos, "_release_sleep", AsyncMock(side_effect=waits.append))
    flaky, state = _flaky_get_db(repos.get_db, 99)
    monkeypatch.setattr(repos, "get_db", flaky)

    with caplog.at_level(logging.WARNING), pytest.raises(OSError):
        await repos.release_claim(nid)

    assert waits == [2, 4, 8, 16, 32]
    assert state["calls"] == 6
    assert any(r.levelno == logging.WARNING and str(nid) in r.getMessage() for r in caplog.records)


async def test_release_claim_no_retry_on_clean_outcome(monkeypatch):
    nid = await _seed("pending")
    sleep = AsyncMock()
    monkeypatch.setattr(repos, "_release_sleep", sleep)

    assert await repos.release_claim(nid) is False

    sleep.assert_not_awaited()


async def test_delete_now_503_after_single_release_attempt_without_waiting(test_client, monkeypatch, caplog):
    """DB outage: one release attempt, no backoff sleep — startup recovery takes over."""
    import backend.db.repositories as repos
    import backend.routers.media as media_mod
    nid = await _seed()
    monkeypatch.setattr(media_mod, "get_by_id", AsyncMock(side_effect=RuntimeError("db down")))
    once = AsyncMock(side_effect=RuntimeError("still down"))
    sleep = AsyncMock()
    monkeypatch.setattr(repos, "_release_claim_once", once)
    monkeypatch.setattr(repos, "_release_sleep", sleep)
    monkeypatch.setattr(media_mod, "_release_claim_once", once)

    with caplog.at_level(logging.ERROR):
        r = test_client.post(f"/api/media/{nid}/delete-now")

    assert r.status_code == 503
    assert once.await_count == 1
    sleep.assert_not_awaited()
    assert "Could not release claim" in caplog.text
