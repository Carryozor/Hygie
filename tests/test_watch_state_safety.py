"""Regression tests — 2026-09-18 incident: a series actively being watched
(Grimm, 78/123 episodes watched, last play the day before) was queued for
deletion and displayed as "never watched".

Four independent defects made that possible; each one is covered below.

1. update_activity_log_batch() used a two-argument MAX(), which is a syntax
   error on MariaDB (aggregate, one argument only). In production the refresh
   of last_played / view_count never ran at all.
2. get_library_user_data() returned a silently truncated (or empty) dict on any
   non-200 response. An empty user-data cache is indistinguishable from "nobody
   ever watched anything", which makes an entire library eligible for deletion.
   Same for get_users() returning [] with no log at all.
3. Consolidated season/series queue entries carry a synthetic emby_id
   ("sonarr-series:304"), so their watch state was copied from an arbitrary
   anchor episode instead of aggregated over the group.
4. Nothing re-checked the watch state before actually deleting: an item watched
   AFTER being queued was still deleted on its delete_at.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import backend.db.engine as _db_engine
import backend.db.schema as _db_schema
import backend.db.settings_store as _db_ss
import backend.db.utils as _db_utils

from backend.db.engine import get_db
from backend.db.schema import init_db


@pytest.fixture
async def fresh_db(monkeypatch, tmp_path):
    """Each test gets its own temporary SQLite DB."""
    db_path = str(tmp_path / "watch_state.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    monkeypatch.setattr(_db_engine, "DIALECT", "sqlite")
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await init_db()
    yield db_path


def _iso(dt: datetime) -> str:
    return dt.isoformat()


async def _insert_pending(emby_id, *, detected_at, delete_at, last_played=None,
                          view_count=0, title="Grimm", library_id="lib1"):
    async with get_db() as db:
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, last_played, view_count, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,'pending')",
            (emby_id, title, "Episode", library_id, "Séries", "/media/s.mkv",
             detected_at, delete_at, last_played, view_count),
        )
        await db.commit()


# ─── Defect 1: two-argument MAX() is not portable to MariaDB ─────────────────

def test_repository_sql_uses_no_two_argument_max():
    """MariaDB's MAX() is an aggregate and takes exactly one argument.

    The test suite runs on SQLite, where MAX(a, b) is a valid scalar function —
    so a functional test can never catch this. Assert on the SQL text instead:
    no statement in repositories.py may use the two-argument form. Use a CASE
    expression (portable to both dialects) instead.
    """
    import inspect

    import backend.db.repositories as repositories

    source = inspect.getsource(repositories)
    offenders = re.findall(r"\bMAX\s*\([^)]*,[^)]*\)", source, re.IGNORECASE)
    assert offenders == [], (
        f"two-argument MAX() is a MariaDB syntax error: {offenders}"
    )


async def test_update_activity_log_batch_sets_last_played_and_view_count(fresh_db):
    """The activity-log refresh must actually write last_played and view_count."""
    from backend.db.repositories import update_activity_log_batch

    now = datetime.now(timezone.utc)
    played = _iso(now - timedelta(days=1))
    await _insert_pending("ep1", detected_at=_iso(now - timedelta(days=3)),
                          delete_at=_iso(now + timedelta(days=4)))

    await update_activity_log_batch([(played, "ep1", played)])

    async with get_db() as db:
        row = await db.fetch_one("SELECT last_played, view_count FROM media_queue WHERE emby_id='ep1'")
    assert row["last_played"] == played
    assert row["view_count"] == 1


async def test_update_activity_log_batch_never_regresses_view_count(fresh_db):
    """An existing higher view_count must not be overwritten with 1."""
    from backend.db.repositories import update_activity_log_batch

    now = datetime.now(timezone.utc)
    played = _iso(now - timedelta(days=1))
    await _insert_pending("ep2", detected_at=_iso(now - timedelta(days=3)),
                          delete_at=_iso(now + timedelta(days=4)), view_count=5)

    await update_activity_log_batch([(played, "ep2", played)])

    async with get_db() as db:
        row = await db.fetch_one("SELECT view_count FROM media_queue WHERE emby_id='ep2'")
    assert row["view_count"] == 5


# ─── Defect 2: missing watch data must fail loudly, never mean "never watched" ─

async def test_get_library_user_data_raises_on_non_200():
    """A non-200 from Emby must raise, not return a silently truncated dict.

    Returning {} makes every item in the library look never-watched, which is
    exactly what queued a fully-watched series for deletion.
    """
    from backend.exceptions import MediaServerUnreachable
    from backend.emby_client import get_library_user_data

    response = MagicMock(status_code=503)
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.emby_client.get_client", new=AsyncMock(return_value=("http://emby:8096", "k"))):
        with patch("backend.emby_client.httpx.AsyncClient", return_value=client):
            with pytest.raises(MediaServerUnreachable):
                await get_library_user_data("user-1", "7", server_id="0")


async def test_get_library_user_data_raises_on_transport_error():
    """A transport failure mid-pagination must raise instead of truncating."""
    from backend.exceptions import MediaServerUnreachable
    from backend.emby_client import get_library_user_data

    client = MagicMock()
    client.get = AsyncMock(side_effect=RuntimeError("connection reset"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.emby_client.get_client", new=AsyncMock(return_value=("http://emby:8096", "k"))):
        with patch("backend.emby_client.httpx.AsyncClient", return_value=client):
            with pytest.raises(MediaServerUnreachable):
                await get_library_user_data("user-1", "7", server_id="0")


async def test_get_users_logs_warning_on_non_200(caplog):
    """get_users() returning [] on an HTTP error must at least be visible."""
    from backend.emby_client import get_users

    response = MagicMock(status_code=401)
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.emby_client.get_client", new=AsyncMock(return_value=("http://emby:8096", "k"))):
        with patch("backend.emby_client.httpx.AsyncClient", return_value=client):
            with caplog.at_level(logging.WARNING, logger="backend.emby_client"):
                users = await get_users(server_id="0")

    assert users == []
    assert any("401" in r.message or "user" in r.message.lower() for r in caplog.records), \
        "a non-200 from /Users must be logged"


async def test_scan_single_server_aborts_when_no_users(fresh_db):
    """No users for an Emby server means watch state is unknowable — not 'unwatched'.

    Scanning anyway marks the whole library never-watched and queues it for
    deletion. The scan must skip the server and raise an alert instead.
    """
    from backend.scanner._orchestrator import _scan_single_server

    scanned = []

    async def _fake_scan_library(lib, user_ids, **kwargs):
        scanned.append(lib)
        return 1

    libs = [{"id": "lib1", "name": "Séries", "emby_library_id": "7", "server_id": "0"}]
    with patch("backend.scanner._orchestrator.get_enabled_libraries",
               new=AsyncMock(return_value=libs)), \
         patch("backend.scanner._orchestrator.ensure_server_uid", new=AsyncMock()), \
         patch("backend.scanner._orchestrator.get_users", new=AsyncMock(return_value=[])), \
         patch("backend.scanner._orchestrator._scan_library", new=_fake_scan_library), \
         patch("backend.scanner._orchestrator.send_alert", new=AsyncMock()) as alert:
        added = await _scan_single_server(
            {"id": "0", "type": "emby", "name": "MB-OS"},
            radarr_cache={}, sonarr_cache={}, seerr_cache={},
        )

    assert scanned == [], "no library may be scanned without watch data"
    assert added == 0
    assert alert.await_count >= 1, "an empty user list must raise an alert"


# ─── Defect 3: consolidated entries must aggregate the group's watch state ───

async def test_consolidated_series_uses_group_max_watch_state(fresh_db):
    """A series entry must carry the most recent play across ALL its episodes.

    Previously it copied last_played/view_count from the anchor episode (the one
    with the highest delete_at), which is arbitrary — an unwatched episode as
    anchor made a fully-watched series display as "never watched".
    """
    from backend.scanner._consolidation import _consolidate_and_insert

    recent = _iso(datetime.now(timezone.utc) - timedelta(days=2))
    old = _iso(datetime.now(timezone.utc) - timedelta(days=300))

    def _ep(ep_id, delete_at, last_played, view_count):
        return {
            "emby_id": ep_id, "title": f"Episode {ep_id}", "media_type": "Episode",
            "library_id": "lib1", "library_name": "Séries",
            "file_path": f"/media/grimm/{ep_id}.mkv", "poster_url": "",
            "tmdb_id": "", "seerr_id": None, "seerr_user_id": None,
            "seerr_username": "", "seerr_request_url": "",
            "radarr_id": None, "sonarr_id": int(ep_id), "sonarr_series_id": 304,
            "season_number": 1, "detected_at": old, "delete_at": delete_at,
            "added_date": old, "last_played": last_played, "view_count": view_count,
        }

    eligible = [
        _ep("1", _iso(datetime.now(timezone.utc) + timedelta(days=7)), None, 0),
        _ep("2", _iso(datetime.now(timezone.utc) + timedelta(days=6)), recent, 3),
    ]
    sonarr_cache = {
        "/media/grimm/1.mkv": {"series_id": 304, "season_number": 1,
                               "series_title": "Grimm", "poster_url": ""},
        "/media/grimm/2.mkv": {"series_id": 304, "season_number": 1,
                               "series_title": "Grimm", "poster_url": ""},
    }

    with patch("backend.scanner._consolidation._insert_queue_entry", new=AsyncMock()) as ins:
        added = await _consolidate_and_insert(
            {"id": "lib1", "name": "Séries"}, eligible, sonarr_cache, "series", set(), False,
        )

    assert added == 1
    entry = ins.await_args.args[0]
    assert entry["emby_id"] == "sonarr-series:304"
    assert entry["last_played"] == recent, "series must carry the most recent play of the group"
    assert entry["view_count"] == 3, "series must carry the highest view count of the group"


async def test_update_consolidated_watch_state_refreshes_pending_row(fresh_db):
    """Pending consolidated rows must be refreshable from their episodes' plays.

    Their emby_id is synthetic, so update_activity_log_batch() (keyed on real
    Emby ids) can never match them — they need their own refresh path.
    """
    from backend.db.repositories import update_consolidated_watch_state

    now = datetime.now(timezone.utc)
    played = _iso(now - timedelta(days=1))
    await _insert_pending("sonarr-series:304", detected_at=_iso(now - timedelta(days=3)),
                          delete_at=_iso(now + timedelta(days=4)))

    await update_consolidated_watch_state([(played, 2, "sonarr-series:304", played)])

    async with get_db() as db:
        row = await db.fetch_one(
            "SELECT last_played, view_count FROM media_queue WHERE emby_id='sonarr-series:304'"
        )
    assert row["last_played"] == played
    assert row["view_count"] == 2


async def test_scan_library_refreshes_pending_consolidated_row(fresh_db):
    """A library scan must refresh the watch state of pending consolidated rows.

    End-to-end version of the incident: the series row already exists with
    last_played=NULL while one of its episodes was played yesterday.
    """
    from backend.scanner._emby_scanner import _scan_library

    now = datetime.now(timezone.utc)
    played = _iso(now - timedelta(days=1))
    await _insert_pending("sonarr-series:304", detected_at=_iso(now - timedelta(days=3)),
                          delete_at=_iso(now + timedelta(days=4)))

    user_id = "user-blork"
    episode = {
        "Id": "ep-42", "Type": "Episode", "Name": "Wesenrein",
        "Path": "/media/grimm/ep42.mkv", "DateCreated": _iso(now - timedelta(days=200)),
    }
    sonarr_cache = {
        "/media/grimm/ep42.mkv": {"ef_id": 42, "series_id": 304, "season_number": 4,
                                  "series_title": "Grimm", "poster_url": ""},
    }
    lib = {
        "id": "lib1", "name": "Séries", "emby_library_id": "7", "server_id": "0",
        "conditions": "[]", "logic": "AND", "grace_days": 7, "deletion_unit": "series",
    }

    async def _items(library_id, limit=500, start=0, server_id="0"):
        return ([episode], 1) if start == 0 else ([], 1)

    with patch("backend.scanner._emby_scanner.get_items_in_library", new=_items), \
         patch("backend.scanner._emby_scanner.get_library_user_data",
               new=AsyncMock(return_value={"ep-42": {"Played": True, "PlayCount": 2}})), \
         patch("backend.scanner._emby_scanner.get_play_activity",
               new=AsyncMock(return_value={"ep-42": played})), \
         patch("backend.scanner._emby_scanner.get_series_tmdb_map",
               new=AsyncMock(return_value={})):
        await _scan_library(lib, [user_id], server_id="0", sonarr_cache=sonarr_cache,
                            queued_ids=set(), ignored_ids=set(), activity_log={"ep-42": played})

    async with get_db() as db:
        row = await db.fetch_one(
            "SELECT last_played, view_count FROM media_queue WHERE emby_id='sonarr-series:304'"
        )
    assert row["last_played"] == played, "the series row must inherit its episodes' plays"
    assert row["view_count"] >= 2


# ─── Defect 4: never delete something watched after it was queued ────────────

async def test_deletion_skips_item_watched_after_being_queued(fresh_db):
    """An item played after its detection date must be rescued, not deleted."""
    from backend.deletion import _rescue_watched_since_queued

    now = datetime.now(timezone.utc)
    detected = _iso(now - timedelta(days=7))
    await _insert_pending("sonarr-series:304", detected_at=detected,
                          delete_at=_iso(now - timedelta(minutes=1)),
                          last_played=_iso(now - timedelta(days=1)))

    async with get_db() as db:
        rows = await db.fetch_all("SELECT * FROM media_queue WHERE status='pending'")

    to_delete = await _rescue_watched_since_queued([dict(r) for r in rows])

    assert to_delete == [], "a series watched yesterday must never be deleted"
    async with get_db() as db:
        left = await db.fetch_all("SELECT * FROM media_queue WHERE emby_id='sonarr-series:304'")
    assert left == [], "the rescued item must be removed from the queue"


async def test_deletion_refreshes_watch_state_from_media_server(fresh_db):
    """The rescue guard must not rely on a DB value that is up to one scan old.

    Scans run every 6 h by default, the deletion job every hour: a play between
    the last scan and the deletion run is invisible in the DB. The due items —
    a handful of rows, not a library sweep — are refreshed live first.
    """
    from backend.deletion import _refresh_watch_state_before_deletion

    now = datetime.now(timezone.utc)
    played = _iso(now - timedelta(hours=2))
    await _insert_pending("ep-live", detected_at=_iso(now - timedelta(days=7)),
                          delete_at=_iso(now - timedelta(minutes=1)))

    async with get_db() as db:
        rows = [dict(r) for r in await db.fetch_all("SELECT * FROM media_queue")]

    with patch("backend.deletion.get_client",
               new=AsyncMock(return_value=("http://emby:8096", "k"))), \
         patch("backend.deletion.get_play_activity",
               new=AsyncMock(return_value={"ep-live": played})):
        refreshed = await _refresh_watch_state_before_deletion(rows, {"lib1": "0"})

    assert [r["emby_id"] for r in refreshed] == ["ep-live"]
    assert refreshed[0]["last_played"] == played, "the live play must reach the guard"


async def test_deletion_postponed_when_watch_state_unreachable(fresh_db):
    """If the media server cannot be reached, deletion waits — it never guesses.

    Deleting on unknown watch state is precisely what destroyed the evidence in
    the first place. The queue is untouched; the next hourly run retries.
    """
    from backend.deletion import _refresh_watch_state_before_deletion
    from backend.exceptions import MediaServerUnreachable

    now = datetime.now(timezone.utc)
    await _insert_pending("ep-blind", detected_at=_iso(now - timedelta(days=7)),
                          delete_at=_iso(now - timedelta(minutes=1)))

    async with get_db() as db:
        rows = [dict(r) for r in await db.fetch_all("SELECT * FROM media_queue")]

    with patch("backend.deletion.get_client",
               new=AsyncMock(return_value=("http://emby:8096", "k"))), \
         patch("backend.deletion.get_play_activity",
               new=AsyncMock(side_effect=MediaServerUnreachable("down"))):
        refreshed = await _refresh_watch_state_before_deletion(rows, {"lib1": "0"})

    assert refreshed == [], "nothing may be deleted while the watch state is unknown"
    async with get_db() as db:
        left = await db.fetch_all("SELECT * FROM media_queue WHERE emby_id='ep-blind'")
    assert len(left) == 1, "the item stays queued for the next run"


async def test_run_deletion_never_touches_a_rescued_item(fresh_db):
    """End-to-end: a rescued item must reach no deletion step at all."""
    from backend.deletion import run_deletion

    now = datetime.now(timezone.utc)
    async with get_db() as db:
        await db.execute(
            "INSERT INTO libraries (id, name, emby_library_id, conditions, logic, "
            "grace_days, seerr_conditions, enabled, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("lib1", "Séries", "7", "[]", "AND", 7, "[]", 1, _iso(now)),
        )
        await db.commit()
    await _insert_pending("sonarr-series:304", detected_at=_iso(now - timedelta(days=7)),
                          delete_at=_iso(now - timedelta(minutes=1)),
                          last_played=_iso(now - timedelta(days=1)))

    with patch("backend.deletion._delete_media", new_callable=AsyncMock) as delete_media, \
         patch("backend.deletion._send_pending_notifications", new_callable=AsyncMock), \
         patch("backend.deletion.sync_emby_collection", new_callable=AsyncMock), \
         patch("backend.deletion.send_alert", new_callable=AsyncMock), \
         patch("backend.deletion.get_client", new=AsyncMock(return_value=("", ""))):
        await run_deletion()

    assert delete_media.await_count == 0, "a rescued item must never enter the deletion pipeline"
    async with get_db() as db:
        left = await db.fetch_all("SELECT * FROM media_queue WHERE emby_id='sonarr-series:304'")
    assert left == []


async def test_reevaluate_returns_503_when_media_server_unreachable():
    """Losing the media server must not silently leave the queue half-evaluated.

    Exercised at the handler level: the conftest TestClient reloads backend.auth
    without reloading this router, so its Depends() still points at the previous
    require_auth object and the dependency override does not apply.
    """
    from fastapi import HTTPException

    from backend.exceptions import MediaServerUnreachable
    from backend.routers.libraries import reevaluate

    with patch("backend.routers.libraries.reevaluate_library_queue",
               new=AsyncMock(side_effect=MediaServerUnreachable("emby down"))):
        with pytest.raises(HTTPException) as exc:
            await reevaluate("lib1", user="tester")

    assert exc.value.status_code == 503
    assert "visionnage" in exc.value.detail


async def test_deletion_proceeds_when_not_watched_since_queueing(fresh_db):
    """The normal case must be untouched: no play since detection → delete."""
    from backend.deletion import _rescue_watched_since_queued

    now = datetime.now(timezone.utc)
    await _insert_pending("ep-old", detected_at=_iso(now - timedelta(days=7)),
                          delete_at=_iso(now - timedelta(minutes=1)),
                          last_played=_iso(now - timedelta(days=300)))
    await _insert_pending("ep-never", detected_at=_iso(now - timedelta(days=7)),
                          delete_at=_iso(now - timedelta(minutes=1)))

    async with get_db() as db:
        rows = await db.fetch_all("SELECT * FROM media_queue WHERE status='pending'")

    to_delete = await _rescue_watched_since_queued([dict(r) for r in rows])

    assert {r["emby_id"] for r in to_delete} == {"ep-old", "ep-never"}
