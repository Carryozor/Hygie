"""Coverage additions for backend/routers/unmonitored.py — baseline was 17%
(102/123 statements missing): only the module import happened via
conftest.py's test_client fixture setup, no endpoint had ever been called.

This module lists Radarr/Sonarr items that are unmonitored-but-on-disk and
lets the user re-enable monitoring or delete them outright — a real
deletion path, so the delete tests assert both the HTTP call made (id,
delete_files flag) and the resulting status, not just a 200.
"""
import httpx
import pytest


@pytest.fixture(autouse=True)
async def _bypass_unmonitored_router_auth(test_client):
    """Same auth-override staleness as backup.py/ignored.py/calendar.py (see
    test_backup_router.py): conftest.py's global override targets the
    post-reload auth_mod.require_auth, but backend.routers.unmonitored was
    imported (and its route dependencies bound) before that reload, so it
    still points at the pre-reload function object."""
    import backend.routers.unmonitored as unmonitored_router_mod
    from backend.db.schema import init_db
    from backend.db.engine import get_db

    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM media_queue")
        await db.execute("DELETE FROM settings WHERE `key` LIKE 'radarr%' OR `key` LIKE 'sonarr%'")
        await db.commit()
    from backend.db.settings_store import _invalidate_settings_cache
    _invalidate_settings_cache()
    test_client.app.dependency_overrides[unmonitored_router_mod.require_auth] = lambda: "testuser"
    yield
    test_client.app.dependency_overrides.pop(unmonitored_router_mod.require_auth, None)


async def _configure_arr(radarr=True, sonarr=True):
    from backend.db.settings_store import set_setting
    if radarr:
        await set_setting("radarr_url", "http://radarr.local")
        await set_setting("radarr_api_key", "rkey")
    if sonarr:
        await set_setting("sonarr_url", "http://sonarr.local")
        await set_setting("sonarr_api_key", "skey")


async def _seed_tracked(radarr_id=None, sonarr_id=None):
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status, radarr_id, sonarr_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("tracked-1", "Tracked", "Movie", "lib1", "Library", "/f/x.mkv",
             "2026-01-01T00:00:00+00:00", "2026-01-08T00:00:00+00:00", "pending",
             radarr_id, sonarr_id),
        )
        await db.commit()


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = {} if json_data is None else json_data

    def json(self):
        return self._json


class _FakeAsyncClient:
    """Routes GET/PUT/DELETE calls by URL substring to canned responses.
    `routes` is a list of (method, substring, response_or_raises) checked
    in order; response_or_raises may be an Exception instance to simulate
    a network failure, or a callable(url, **kwargs) for call-recording."""

    def __init__(self, routes, calls=None, **kwargs):
        self._routes = routes
        self._calls = calls if calls is not None else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def _resolve(self, method, url, **kwargs):
        self._calls.append((method, url, kwargs))
        for m, sub, resp in self._routes:
            if m == method and sub in url:
                if isinstance(resp, Exception):
                    raise resp
                if callable(resp):
                    return resp(url, **kwargs)
                return resp
        raise AssertionError(f"No fake route for {method} {url}")

    async def get(self, url, **kwargs):
        return await self._resolve("GET", url, **kwargs)

    async def put(self, url, **kwargs):
        return await self._resolve("PUT", url, **kwargs)

    async def delete(self, url, **kwargs):
        return await self._resolve("DELETE", url, **kwargs)


def _patch_client(monkeypatch, routes, calls=None):
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(routes, calls))


# ─── list_unmonitored ──────────────────────────────────────────────────────

async def test_list_unmonitored_returns_empty_when_arr_not_configured(test_client):
    r = test_client.get("/api/unmonitored")
    assert r.status_code == 200
    assert r.json() == {"movies": [], "series": []}


async def test_list_unmonitored_filters_monitored_and_no_file(test_client, monkeypatch):
    await _configure_arr()
    movies = [
        {"id": 1, "title": "Qualifies", "year": 2020, "sizeOnDisk": 100, "tmdbId": 11,
         "monitored": False, "hasFile": True,
         "images": [{"coverType": "banner", "remoteUrl": "http://x/banner.jpg"},
                    {"coverType": "poster", "remoteUrl": "http://x/poster.jpg"}]},
        {"id": 2, "title": "Still monitored", "monitored": True, "hasFile": True},
        {"id": 3, "title": "No file", "monitored": False, "hasFile": False},
    ]
    series = []
    routes = [
        ("GET", "/api/v3/movie", _FakeResponse(200, movies)),
        ("GET", "/api/v3/series", _FakeResponse(200, series)),
    ]
    _patch_client(monkeypatch, routes)

    r = test_client.get("/api/unmonitored")
    assert r.status_code == 200
    body = r.json()
    assert [m["title"] for m in body["movies"]] == ["Qualifies"]
    assert body["movies"][0]["id"] == 1
    assert body["movies"][0]["poster_url"] == "http://x/poster.jpg"
    assert body["series"] == []


async def test_list_unmonitored_excludes_already_tracked_movie(test_client, monkeypatch):
    """A movie whose radarr_id already sits in a pending media_queue row is
    being handled by the normal deletion pipeline — it must not also show
    up as 'unmonitored/orphaned', or the user could double-act on it."""
    await _configure_arr()
    await _seed_tracked(radarr_id=42)
    movies = [
        {"id": 42, "title": "Already tracked", "monitored": False, "hasFile": True},
        {"id": 43, "title": "Not tracked", "monitored": False, "hasFile": True},
    ]
    routes = [
        ("GET", "/api/v3/movie", _FakeResponse(200, movies)),
        ("GET", "/api/v3/series", _FakeResponse(200, [])),
    ]
    _patch_client(monkeypatch, routes)

    r = test_client.get("/api/unmonitored")
    titles = [m["title"] for m in r.json()["movies"]]
    assert titles == ["Not tracked"]


async def test_list_unmonitored_search_is_case_insensitive_substring(test_client, monkeypatch):
    await _configure_arr()
    movies = [
        {"id": 1, "title": "The Matrix", "monitored": False, "hasFile": True},
        {"id": 2, "title": "Inception", "monitored": False, "hasFile": True},
    ]
    routes = [
        ("GET", "/api/v3/movie", _FakeResponse(200, movies)),
        ("GET", "/api/v3/series", _FakeResponse(200, [])),
    ]
    _patch_client(monkeypatch, routes)

    r = test_client.get("/api/unmonitored?search=matrix")
    titles = [m["title"] for m in r.json()["movies"]]
    assert titles == ["The Matrix"]


async def test_list_unmonitored_series_full_shape_with_poster(test_client, monkeypatch):
    """Exercise the series branch end-to-end: monitored/no-episode/tracked
    exclusions, search filter, and the poster-url selection loop (picks the
    first http(s) poster, matching the movie-side behavior)."""
    await _configure_arr()
    await _seed_tracked(sonarr_id=99)
    series = [
        {"id": 1, "title": "Wanted Show", "year": 2019, "monitored": False,
         "tvdbId": 555, "statistics": {"episodeFileCount": 10, "sizeOnDisk": 900},
         "images": [{"coverType": "banner", "remoteUrl": "http://x/banner.jpg"},
                    {"coverType": "poster", "remoteUrl": "http://x/poster.jpg"}]},
        {"id": 2, "title": "Still monitored", "monitored": True,
         "statistics": {"episodeFileCount": 5}},
        {"id": 3, "title": "No episodes", "monitored": False, "statistics": {}},
        {"id": 99, "title": "Already tracked", "monitored": False,
         "statistics": {"episodeFileCount": 2}},
    ]
    routes = [
        ("GET", "/api/v3/movie", _FakeResponse(200, [])),
        ("GET", "/api/v3/series", _FakeResponse(200, series)),
    ]
    _patch_client(monkeypatch, routes)

    r = test_client.get("/api/unmonitored")
    assert r.status_code == 200
    result = r.json()["series"]
    assert len(result) == 1
    assert result[0] == {
        "id": 1, "title": "Wanted Show", "year": 2019, "size_bytes": 900,
        "poster_url": "http://x/poster.jpg", "tvdb_id": 555,
        "episode_count": 10, "type": "series",
    }

    r2 = test_client.get("/api/unmonitored?search=nomatch")
    assert r2.json()["series"] == []


async def test_list_unmonitored_swallows_sonarr_failure(test_client, monkeypatch):
    """A Sonarr outage must not break the whole endpoint — Radarr results
    should still come back."""
    await _configure_arr()
    movies = [{"id": 1, "title": "OK Movie", "monitored": False, "hasFile": True}]
    routes = [
        ("GET", "/api/v3/movie", _FakeResponse(200, movies)),
        ("GET", "/api/v3/series", httpx.ConnectError("boom")),
    ]
    _patch_client(monkeypatch, routes)

    r = test_client.get("/api/unmonitored")
    assert r.status_code == 200
    body = r.json()
    assert [m["title"] for m in body["movies"]] == ["OK Movie"]
    assert body["series"] == []


async def test_list_unmonitored_swallows_radarr_failure_and_still_returns_series(test_client, monkeypatch):
    """A Radarr outage must not break the whole endpoint — Sonarr results
    should still come back (each arr call is independently try/excepted)."""
    await _configure_arr()
    series = [{"id": 9, "title": "Some Show", "monitored": False,
               "statistics": {"episodeFileCount": 3, "sizeOnDisk": 500}}]
    routes = [
        ("GET", "/api/v3/movie", httpx.ConnectError("boom")),
        ("GET", "/api/v3/series", _FakeResponse(200, series)),
    ]
    _patch_client(monkeypatch, routes)

    r = test_client.get("/api/unmonitored")
    assert r.status_code == 200
    body = r.json()
    assert body["movies"] == []
    assert [s["title"] for s in body["series"]] == ["Some Show"]


# ─── monitor_movie / monitor_series ────────────────────────────────────────

async def test_monitor_movie_returns_400_when_radarr_not_configured(test_client):
    r = test_client.post("/api/unmonitored/monitor/movie/1")
    assert r.status_code == 400


async def test_monitor_movie_returns_404_when_not_found_in_radarr(test_client, monkeypatch):
    await _configure_arr()
    routes = [("GET", "/api/v3/movie/1", _FakeResponse(404))]
    _patch_client(monkeypatch, routes)

    r = test_client.post("/api/unmonitored/monitor/movie/1")
    assert r.status_code == 404


async def test_monitor_movie_success_sends_monitored_true_and_logs(test_client, monkeypatch):
    await _configure_arr()
    calls = []
    routes = [
        ("GET", "/api/v3/movie/7", _FakeResponse(200, {"id": 7, "monitored": False, "title": "X"})),
        ("PUT", "/api/v3/movie/7", _FakeResponse(202)),
    ]
    _patch_client(monkeypatch, routes, calls)

    r = test_client.post("/api/unmonitored/monitor/movie/7")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

    put_calls = [c for c in calls if c[0] == "PUT"]
    assert len(put_calls) == 1
    assert put_calls[0][2]["json"]["monitored"] is True

    from backend.db.engine import get_db
    async with get_db() as db:
        logs = await db.fetch_all("SELECT message FROM logs ORDER BY id DESC LIMIT 20")
    assert any("Radarr movie #7" in row["message"] for row in logs)


async def test_monitor_movie_arr_put_error_returns_500(test_client, monkeypatch):
    await _configure_arr()
    routes = [
        ("GET", "/api/v3/movie/7", _FakeResponse(200, {"id": 7, "monitored": False})),
        ("PUT", "/api/v3/movie/7", _FakeResponse(500)),
    ]
    _patch_client(monkeypatch, routes)

    r = test_client.post("/api/unmonitored/monitor/movie/7")
    assert r.status_code == 500


async def test_monitor_series_returns_400_when_sonarr_not_configured(test_client):
    r = test_client.post("/api/unmonitored/monitor/series/1")
    assert r.status_code == 400


async def test_monitor_series_returns_404_when_not_found(test_client, monkeypatch):
    await _configure_arr()
    routes = [("GET", "/api/v3/series/5", _FakeResponse(404))]
    _patch_client(monkeypatch, routes)

    r = test_client.post("/api/unmonitored/monitor/series/5")
    assert r.status_code == 404


async def test_monitor_series_arr_put_error_returns_500(test_client, monkeypatch):
    await _configure_arr()
    routes = [
        ("GET", "/api/v3/series/5", _FakeResponse(200, {"id": 5, "monitored": False})),
        ("PUT", "/api/v3/series/5", _FakeResponse(500)),
    ]
    _patch_client(monkeypatch, routes)

    r = test_client.post("/api/unmonitored/monitor/series/5")
    assert r.status_code == 500


async def test_monitor_series_success(test_client, monkeypatch):
    await _configure_arr()
    routes = [
        ("GET", "/api/v3/series/5", _FakeResponse(200, {"id": 5, "monitored": False})),
        ("PUT", "/api/v3/series/5", _FakeResponse(200)),
    ]
    _patch_client(monkeypatch, routes)

    r = test_client.post("/api/unmonitored/monitor/series/5")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ─── delete_movie / delete_series — actual deletion paths ─────────────────

async def test_delete_movie_calls_radarr_delete_with_requested_flag(test_client, monkeypatch):
    from unittest.mock import AsyncMock
    import backend.arr_clients as arr_clients_mod

    fake_delete = AsyncMock(return_value=True)
    monkeypatch.setattr(arr_clients_mod, "radarr_delete", fake_delete)

    r = test_client.delete("/api/unmonitored/movie/9?delete_files=true")
    assert r.status_code == 200
    assert r.json() == {"status": "deleted"}
    fake_delete.assert_awaited_once_with(9, delete_files=True)


async def test_delete_movie_defaults_delete_files_false(test_client, monkeypatch):
    from unittest.mock import AsyncMock
    import backend.arr_clients as arr_clients_mod

    fake_delete = AsyncMock(return_value=True)
    monkeypatch.setattr(arr_clients_mod, "radarr_delete", fake_delete)

    r = test_client.delete("/api/unmonitored/movie/9")
    assert r.status_code == 200
    fake_delete.assert_awaited_once_with(9, delete_files=False)


async def test_delete_movie_returns_500_when_radarr_delete_fails(test_client, monkeypatch):
    from unittest.mock import AsyncMock
    import backend.arr_clients as arr_clients_mod

    monkeypatch.setattr(arr_clients_mod, "radarr_delete", AsyncMock(return_value=False))

    r = test_client.delete("/api/unmonitored/movie/9")
    assert r.status_code == 500


async def test_delete_series_returns_400_when_sonarr_not_configured(test_client):
    r = test_client.delete("/api/unmonitored/series/3")
    assert r.status_code == 400


async def test_delete_series_success_passes_delete_files_param(test_client, monkeypatch):
    await _configure_arr()
    calls = []
    routes = [("DELETE", "/api/v3/series/3", _FakeResponse(200))]
    _patch_client(monkeypatch, routes, calls)

    r = test_client.delete("/api/unmonitored/series/3?delete_files=true")
    assert r.status_code == 200
    assert r.json() == {"status": "deleted"}

    delete_calls = [c for c in calls if c[0] == "DELETE"]
    assert delete_calls[0][2]["params"]["deleteFiles"] == "true"


async def test_delete_series_returns_500_on_sonarr_error(test_client, monkeypatch):
    await _configure_arr()
    routes = [("DELETE", "/api/v3/series/3", _FakeResponse(500))]
    _patch_client(monkeypatch, routes)

    r = test_client.delete("/api/unmonitored/series/3")
    assert r.status_code == 500


# ─── AUTH ───────────────────────────────────────────────────────────────────

def test_list_unmonitored_requires_auth(test_client):
    import backend.routers.unmonitored as unmonitored_router_mod
    override = test_client.app.dependency_overrides.pop(unmonitored_router_mod.require_auth, None)
    try:
        r = test_client.get("/api/unmonitored")
        assert r.status_code == 401
    finally:
        if override is not None:
            test_client.app.dependency_overrides[unmonitored_router_mod.require_auth] = override
