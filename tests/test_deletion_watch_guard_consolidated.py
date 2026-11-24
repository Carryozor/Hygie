"""Watch-state guard for consolidated (sonarr-series / sonarr-season) rows.

A consolidated row has no per-item media-server Id: its last play is found by
resolving the Series/Season item from its path, then walking its episodes in
the activity log. Every step used to swallow its errors and answer "no play",
so a media-server hiccup, a series Sonarr no longer knows or a series of more
than 500 episodes made a watched series look never-watched. Now an unknown
state postpones the row (it stays queued) and the episode walk is paginated.
"""
import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from backend.exceptions import MediaServerUnreachable

URL = "http://emby.guard:8096"
LIB = {"lib1": "0"}


def _row(**overrides) -> dict:
    base = {
        "id": 7,
        "emby_id": "sonarr-series:304",
        "title": "Grimm",
        "sonarr_series_id": 304,
        "arr_server_url": "http://sonarr-b",
        "season_number": None,
        "file_path": "/media/grimm/s01e01.mkv",
        "last_played": None,
        "view_count": 0,
        "library_id": "lib1",
        "detected_at": "2026-11-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
async def _fresh_db(monkeypatch, tmp_path):
    import backend.db.engine as _db_engine
    import backend.db.schema as _db_schema
    import backend.db.settings_store as _db_ss
    import backend.db.utils as _db_utils
    import backend.deletion as _deletion

    db_path = str(tmp_path / "guard.db")
    for mod, attr in ((_db_utils, "DB_PATH"), (_db_schema, "DB_PATH"),
                      (_deletion, "DB_PATH"), (_db_engine, "SQLITE_PATH")):
        monkeypatch.setattr(mod, attr, db_path)
    monkeypatch.setattr(_db_engine, "DIALECT", "sqlite")
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()


@pytest.fixture
def emby(monkeypatch):
    """A configured media server."""
    async def _client(server_id="0"):
        return URL, "k"
    monkeypatch.setattr("backend.emby_client.get_client", _client)
    monkeypatch.setattr("backend.deletion.get_client", _client)


@pytest.fixture
def guard_env(emby):
    """Everything around the guard mocked except the Emby HTTP traffic."""
    with (
        patch("backend.deletion.get_play_activity", new=AsyncMock(return_value={})) as activity,
        patch("backend.media_item_resolver.sonarr_get_series_by_id_any",
              new=AsyncMock(return_value={"path": "/media/grimm"})) as sonarr,
        patch("backend.deletion.update_activity_log_batch", new=AsyncMock()),
        patch("backend.deletion.update_consolidated_watch_state", new=AsyncMock()),
        patch("backend.deletion.add_log", new=AsyncMock()) as add_log,
        patch("backend.deletion.delete_by_id", new=AsyncMock()),
    ):
        yield type("Env", (), {"activity": activity, "sonarr": sonarr, "add_log": add_log})


def _episodes(start: int, n: int) -> list:
    return [{"Id": f"ep-{i}"} for i in range(start, start + n)]


def _series_hit() -> httpx.Response:
    return httpx.Response(200, json={"Items": [{"Id": "series-id", "Path": "/media/grimm"}]})


# --- P1-1 RED: the guard on the full path -----------------------------------

@respx.mock
async def test_consolidated_row_postponed_when_emby_lookup_errors(guard_env):
    from backend.deletion import _refresh_watch_state_before_deletion

    respx.get(f"{URL}/Items").mock(return_value=httpx.Response(500))
    result = await _refresh_watch_state_before_deletion([_row()], LIB)
    assert result == []


@respx.mock
async def test_recent_play_beyond_first_500_episodes_rescues_row(guard_env):
    from backend.deletion import _refresh_watch_state_before_deletion, _rescue_watched_since_queued

    def _items(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if params.get("IncludeItemTypes") == "Series":
            return _series_hit()
        start = int(params["StartIndex"])
        page = _episodes(0, 500) if start == 0 else _episodes(500, 100)
        return httpx.Response(200, json={"Items": page, "TotalRecordCount": 600})

    respx.get(f"{URL}/Items").mock(side_effect=_items)
    guard_env.activity.return_value = {"ep-599": "2026-11-10T00:00:00+00:00"}

    refreshed = await _refresh_watch_state_before_deletion([_row()], LIB)
    final = await _rescue_watched_since_queued(refreshed)

    assert final == []


@respx.mock
async def test_page_failure_midway_postpones_instead_of_truncating(guard_env):
    """Page 1 answers, page 2 fails: the walk is unknown, not 'first 500 only'."""
    from backend.deletion import _refresh_watch_state_before_deletion

    def _items(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if params.get("IncludeItemTypes") == "Series":
            return _series_hit()
        if int(params["StartIndex"]) == 0:
            return httpx.Response(200, json={"Items": _episodes(0, 500), "TotalRecordCount": 600})
        return httpx.Response(500)

    respx.get(f"{URL}/Items").mock(side_effect=_items)
    result = await _refresh_watch_state_before_deletion([_row()], LIB)
    assert result == []


@respx.mock
async def test_postponed_row_logs_a_french_warning_naming_the_title(guard_env, caplog):
    from backend.deletion import _refresh_watch_state_before_deletion

    respx.get(f"{URL}/Items").mock(return_value=httpx.Response(500))
    with caplog.at_level(logging.WARNING, logger="backend.deletion"):
        await _refresh_watch_state_before_deletion([_row()], LIB)

    messages = [c.args[1] for c in guard_env.add_log.await_args_list]
    assert any(
        "Suppression reportée" in m and "Grimm" in m and "état de visionnage inconnu" in m
        for m in messages
    ), messages
    assert all(c.args[0] == "WARN" for c in guard_env.add_log.await_args_list)
    assert any("Deletion postponed" in r.getMessage() for r in caplog.records)


@respx.mock
async def test_only_the_unknown_row_is_postponed(guard_env):
    """A direct row and a known consolidated row in the same batch are unaffected."""
    from backend.deletion import _refresh_watch_state_before_deletion

    direct = {"id": 1, "emby_id": "ep-1", "title": "Movie", "library_id": "lib1",
              "last_played": None, "detected_at": None}
    guard_env.sonarr.return_value = None  # Sonarr no longer knows the series
    result = await _refresh_watch_state_before_deletion([direct, _row()], LIB)
    assert result == [direct]


@respx.mock
async def test_single_page_series_without_play_stays_in_batch(guard_env):
    """A fully-read library with no recent play is genuinely 'not watched'."""
    from backend.deletion import _refresh_watch_state_before_deletion

    def _items(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("IncludeItemTypes") == "Series":
            return _series_hit()
        return httpx.Response(200, json={"Items": _episodes(0, 3), "TotalRecordCount": 3})

    respx.get(f"{URL}/Items").mock(side_effect=_items)
    row = _row()
    assert await _refresh_watch_state_before_deletion([row], LIB) == [row]


# --- _consolidated_last_play raises WatchStateUnknown -----------------------

async def test_last_play_unknown_when_target_not_found(emby):
    from backend.deletion import WatchStateUnknown, _consolidated_last_play

    with (
        patch("backend.media_item_resolver.sonarr_get_series_by_id_any",
              new=AsyncMock(return_value={"path": "/media/grimm"})),
        patch("backend.media_item_resolver.find_item_by_path", new=AsyncMock(return_value=None)),
        pytest.raises(WatchStateUnknown),
    ):
        await _consolidated_last_play(_row(), "0", {})


@pytest.mark.parametrize("series", [None, {}, {"path": ""}])
async def test_last_play_unknown_when_sonarr_does_not_know_series(emby, series):
    from backend.deletion import WatchStateUnknown, _consolidated_last_play

    with (
        patch("backend.media_item_resolver.sonarr_get_series_by_id_any",
              new=AsyncMock(return_value=series)),
        pytest.raises(WatchStateUnknown),
    ):
        await _consolidated_last_play(_row(), "0", {})


async def test_last_play_unknown_when_season_folder_cannot_be_derived(emby):
    from backend.deletion import WatchStateUnknown, _consolidated_last_play

    with (
        patch("backend.media_item_resolver.sonarr_get_series_by_id_any",
              new=AsyncMock(return_value={"path": "/media/grimm"})),
        pytest.raises(WatchStateUnknown),
    ):
        await _consolidated_last_play(_row(season_number=2, file_path=""), "0", {})


async def test_last_play_unknown_when_media_server_unreachable(emby):
    from backend.deletion import WatchStateUnknown, _consolidated_last_play

    with (
        patch("backend.media_item_resolver.sonarr_get_series_by_id_any",
              new=AsyncMock(return_value={"path": "/media/grimm"})),
        patch("backend.media_item_resolver.find_item_by_path",
              new=AsyncMock(side_effect=MediaServerUnreachable("down"))),
        pytest.raises(WatchStateUnknown),
    ):
        await _consolidated_last_play(_row(), "0", {})


async def test_last_play_unknown_when_episode_walk_fails(emby):
    from backend.deletion import WatchStateUnknown, _consolidated_last_play

    async def _boom(*a, **k):
        raise MediaServerUnreachable("page 2 failed")
        yield  # pragma: no cover - makes this an async generator

    with (
        patch("backend.media_item_resolver.sonarr_get_series_by_id_any",
              new=AsyncMock(return_value={"path": "/media/grimm"})),
        patch("backend.media_item_resolver.find_item_by_path",
              new=AsyncMock(return_value={"Id": "series-id"})),
        patch("backend.emby_client.iter_library_items_strict", new=_boom),
        pytest.raises(WatchStateUnknown),
    ):
        await _consolidated_last_play(_row(), "0", {})


async def test_last_play_forwards_arr_server_url_to_sonarr_lookup(emby):
    """N1 (guard side): the row's own Sonarr server resolves the series."""
    from backend.deletion import _consolidated_last_play

    async def _none(*a, **k):
        return
        yield  # pragma: no cover

    with (
        patch("backend.media_item_resolver.sonarr_get_series_by_id_any",
              new=AsyncMock(return_value={"path": "/media/grimm"})) as sonarr,
        patch("backend.media_item_resolver.find_item_by_path",
              new=AsyncMock(return_value={"Id": "series-id"})) as find,
        patch("backend.emby_client.iter_library_items_strict", new=_none),
    ):
        await _consolidated_last_play(_row(), "0", {})

    sonarr.assert_awaited_once_with(304, arr_server_url="http://sonarr-b")
    assert find.await_args.kwargs["strict"] is True


# --- emby_client strict variants --------------------------------------------

@respx.mock
async def test_find_item_by_path_strict_raises_on_http_error(emby):
    from backend.emby_client import find_item_by_path

    respx.get(f"{URL}/Items").mock(return_value=httpx.Response(500))
    with pytest.raises(MediaServerUnreachable):
        await find_item_by_path("/media/g", server_id="s-http", strict=True)


@respx.mock
async def test_find_item_by_path_strict_raises_on_transport_error(emby):
    from backend.emby_client import find_item_by_path

    respx.get(f"{URL}/Items").mock(side_effect=RuntimeError("boom"))
    with pytest.raises(MediaServerUnreachable):
        await find_item_by_path("/media/g", server_id="s-net", strict=True)


@respx.mock
async def test_find_item_by_path_strict_raises_on_unparseable_body(emby):
    from backend.emby_client import find_item_by_path

    respx.get(f"{URL}/Items").mock(return_value=httpx.Response(200, content=b"<html>"))
    with pytest.raises(MediaServerUnreachable):
        await find_item_by_path("/media/g", server_id="s-json", strict=True)


async def test_find_item_by_path_strict_raises_when_server_unconfigured(monkeypatch):
    from backend.emby_client import find_item_by_path

    async def _none(server_id="0"):
        return "", ""
    monkeypatch.setattr("backend.emby_client.get_client", _none)
    with pytest.raises(MediaServerUnreachable):
        await find_item_by_path("/media/g", server_id="s-cfg", strict=True)


async def test_find_item_by_path_breaker_open_strict_raises_default_returns_none(emby):
    from backend.arr_clients.circuit_breaker import CircuitOpenError
    from backend.emby_client import find_item_by_path

    class _Open:
        async def call(self, fn):
            raise CircuitOpenError("open")
    with patch("backend.emby_client._emby_breaker", return_value=_Open()):
        with pytest.raises(MediaServerUnreachable):
            await find_item_by_path("/media/g", server_id="s-brk", strict=True)
        assert await find_item_by_path("/media/g", server_id="s-brk") is None


@respx.mock
async def test_find_item_by_path_strict_returns_none_on_genuine_miss(emby):
    from backend.emby_client import find_item_by_path

    respx.get(f"{URL}/Items").mock(return_value=httpx.Response(200, json={"Items": []}))
    assert await find_item_by_path("/media/g", server_id="s-miss", strict=True) is None


@respx.mock
async def test_find_item_by_path_default_mode_still_swallows_errors(emby):
    from backend.emby_client import find_item_by_path

    respx.get(f"{URL}/Items").mock(return_value=httpx.Response(500))
    assert await find_item_by_path("/media/g", server_id="s-lax") is None
    respx.get(f"{URL}/Items").mock(side_effect=RuntimeError("boom"))
    assert await find_item_by_path("/media/g", server_id="s-lax") is None


@respx.mock
async def test_iter_library_items_strict_walks_every_page(emby):
    from backend.emby_client import iter_library_items_strict

    def _items(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["StartIndex"])
        page = _episodes(0, 2) if start == 0 else _episodes(2, 1)
        return httpx.Response(200, json={"Items": page, "TotalRecordCount": 3})

    route = respx.get(f"{URL}/Items").mock(side_effect=_items)
    ids = [i["Id"] async for i in iter_library_items_strict("lib", server_id="s-it1", page_size=2)]
    assert ids == ["ep-0", "ep-1", "ep-2"]
    assert route.call_count == 2


@respx.mock
async def test_iter_library_items_strict_raises_when_listing_stalls(emby):
    from backend.emby_client import iter_library_items_strict

    def _items(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["StartIndex"])
        page = _episodes(0, 2) if start == 0 else []
        return httpx.Response(200, json={"Items": page, "TotalRecordCount": 5})

    respx.get(f"{URL}/Items").mock(side_effect=_items)
    with pytest.raises(MediaServerUnreachable):
        _ = [i async for i in iter_library_items_strict("lib", server_id="s-it2", page_size=2)]


@respx.mock
async def test_iter_library_items_strict_raises_on_transport_error(emby):
    from backend.emby_client import iter_library_items_strict

    respx.get(f"{URL}/Items").mock(side_effect=RuntimeError("boom"))
    with pytest.raises(MediaServerUnreachable):
        _ = [i async for i in iter_library_items_strict("lib", server_id="s-it3")]


async def test_iter_library_items_strict_raises_when_unconfigured_or_breaker_open(monkeypatch, emby):
    from backend.arr_clients.circuit_breaker import CircuitOpenError
    from backend.emby_client import iter_library_items_strict

    class _Open:
        async def call(self, fn):
            raise CircuitOpenError("open")
    with patch("backend.emby_client._emby_breaker", return_value=_Open()):
        with pytest.raises(MediaServerUnreachable):
            _ = [i async for i in iter_library_items_strict("lib", server_id="s-it4")]

    async def _none(server_id="0"):
        return "", ""
    monkeypatch.setattr("backend.emby_client.get_client", _none)
    with pytest.raises(MediaServerUnreachable):
        _ = [i async for i in iter_library_items_strict("lib", server_id="s-it5")]


@respx.mock
async def test_iter_library_items_strict_raises_on_http_error(emby):
    from backend.emby_client import iter_library_items_strict

    respx.get(f"{URL}/Items").mock(return_value=httpx.Response(503))
    with pytest.raises(MediaServerUnreachable):
        _ = [i async for i in iter_library_items_strict("lib", server_id="s-it6")]


# --- run_ignored_cleanup: errors are warnings, not debug noise --------------

async def test_cleanup_failures_are_logged_as_warnings(tmp_path, monkeypatch, caplog):
    import backend.deletion as _deletion

    # The retention / logs / job_history blocks each call get_int_setting in their try.
    with (
        patch("backend.deletion.get_int_setting", new=AsyncMock(side_effect=RuntimeError("db locked"))),
        caplog.at_level(logging.WARNING, logger="backend.deletion"),
    ):
        await _deletion.run_ignored_cleanup()
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    for label in ("Purge retention", "Purge logs", "Purge job_history"):
        assert any(label in m for m in warned), (label, warned)

    # VACUUM: a DB_PATH that cannot be opened.
    caplog.clear()
    monkeypatch.setattr(_deletion, "DB_PATH", str(tmp_path / "missing-dir" / "x.db"))
    with (
        patch("backend.deletion.delete_stale_deleted", new=AsyncMock(return_value=1500)),
        caplog.at_level(logging.WARNING, logger="backend.deletion"),
    ):
        await _deletion.run_ignored_cleanup()
    assert any("VACUUM" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)
