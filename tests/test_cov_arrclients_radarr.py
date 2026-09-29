"""Coverage tests for backend/arr_clients/radarr.py.

Priority per the mission brief: delete calls must hit the exact server/id
with the exact deleteFiles value, multi-server resolution must never fall
back to guessing a server, and network failures must degrade gracefully
(never crash the deletion pipeline).
"""
import os

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")

import httpx
import pytest
from pytest_httpx import HTTPXMock

import backend.arr_clients.radarr as radarr


RADARR_URL = "http://radarr.test:7878"
RADARR_KEY = "radarr-test-key"


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    async def _config():
        return RADARR_URL, RADARR_KEY
    monkeypatch.setattr(radarr, "_radarr_config", _config)


# ─── build_radarr_path_cache ───────────────────────────────────────────────────
# These go through the REAL with_retry + real httpx.AsyncClient (only the
# HTTP transport is mocked via httpx_mock) so the inner _fetch closure's own
# status-code handling (line 52) and the id-filter (line 58) actually run —
# a monkeypatched with_retry/AsyncClient would bypass that code entirely and
# let a broken filter pass unnoticed.

async def test_build_path_cache_skips_movie_with_no_id(monkeypatch, httpx_mock: HTTPXMock):
    async def _servers():
        return [{"url": RADARR_URL, "api_key": RADARR_KEY}]
    monkeypatch.setattr(radarr, "get_radarr_servers", _servers)

    httpx_mock.add_response(
        url=f"{RADARR_URL}/api/v3/movie",
        json=[
            {"path": "/data/movies/noid", "movieFile": {"path": "/data/movies/noid/x.mkv"}},
            {"id": 9, "path": "/data/movies/ok", "movieFile": {"path": "/data/movies/ok/x.mkv"}},
        ],
    )

    cache = await radarr.build_radarr_path_cache()
    assert "/data/movies/noid/x.mkv" not in cache
    assert cache["/data/movies/ok/x.mkv"] == (9, RADARR_URL, RADARR_KEY)


async def test_build_path_cache_indexes_both_file_and_folder_paths(monkeypatch, httpx_mock: HTTPXMock):
    async def _servers():
        return [{"url": RADARR_URL, "api_key": RADARR_KEY}]
    monkeypatch.setattr(radarr, "get_radarr_servers", _servers)

    httpx_mock.add_response(
        url=f"{RADARR_URL}/api/v3/movie",
        json=[{
            "id": 7, "path": "/data/movies/dune",
            "movieFile": {"path": "/data/movies/dune/dune.mkv"},
        }],
    )

    cache = await radarr.build_radarr_path_cache()
    assert cache["/data/movies/dune/dune.mkv"] == (7, RADARR_URL, RADARR_KEY)
    assert cache["/data/movies/dune"] == (7, RADARR_URL, RADARR_KEY)


async def test_build_path_cache_ignores_non_200_response(monkeypatch, httpx_mock: HTTPXMock):
    """A non-200 from one server's /movie listing must yield an empty (not
    crashing) cache contribution for that server — hits the inner _fetch's
    own status-code branch, not the outer exception handler."""
    async def _servers():
        return [{"url": RADARR_URL, "api_key": RADARR_KEY}]
    monkeypatch.setattr(radarr, "get_radarr_servers", _servers)

    httpx_mock.add_response(url=f"{RADARR_URL}/api/v3/movie", status_code=500)

    cache = await radarr.build_radarr_path_cache()
    assert cache == {}


async def test_build_path_cache_continues_after_one_server_fails(monkeypatch, httpx_mock: HTTPXMock):
    """One server erroring must not abort caching the others."""
    async def _servers_fn():
        return [
            {"url": "http://bad.test:1", "api_key": "kb"},
            {"url": "http://good.test:1", "api_key": "kg"},
        ]
    monkeypatch.setattr(radarr, "get_radarr_servers", _servers_fn)

    async def _no_sleep(_delay):
        return None
    monkeypatch.setattr("backend.arr_clients.retry.asyncio.sleep", _no_sleep)

    httpx_mock.add_exception(
        httpx.ConnectError("down"), url="http://bad.test:1/api/v3/movie", is_reusable=True
    )
    httpx_mock.add_response(
        url="http://good.test:1/api/v3/movie",
        json=[{"id": 1, "path": "/data/m", "movieFile": {"path": "/data/m/m.mkv"}}],
    )

    cache = await radarr.build_radarr_path_cache()
    assert cache["/data/m/m.mkv"] == (1, "http://good.test:1", "kg")


# ─── radarr_find_by_path_cached ────────────────────────────────────────────────

def test_find_by_path_cached_exact_match():
    cache = {"/data/movies/x/x.mkv": (1, "u", "k")}
    assert radarr.radarr_find_by_path_cached("/data/movies/x/x.mkv", cache) == (1, "u", "k")


def test_find_by_path_cached_folder_prefix_match():
    cache = {"/data/movies/x": (1, "u", "k")}
    assert radarr.radarr_find_by_path_cached("/data/movies/x/x.mkv", cache) == (1, "u", "k")


def test_find_by_path_cached_no_match_returns_none():
    cache = {"/data/movies/x": (1, "u", "k")}
    assert radarr.radarr_find_by_path_cached("/data/movies/y/y.mkv", cache) is None


def test_find_by_path_cached_empty_inputs_return_none():
    assert radarr.radarr_find_by_path_cached("", {"a": 1}) is None
    assert radarr.radarr_find_by_path_cached("/x", {}) is None


# ─── radarr_find_by_path (live HTTP, multi-server) ─────────────────────────────

class _FakeResp:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data
    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, get_fn):
        self._get_fn = get_fn
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def get(self, url, headers=None, params=None):
        return await self._get_fn()


async def test_find_by_path_returns_none_for_empty_path():
    assert await radarr.radarr_find_by_path("") is None


async def test_find_by_path_matches_across_multiple_servers(monkeypatch):
    servers = [
        {"url": "http://a:1", "api_key": "ka"},
        {"url": "http://b:1", "api_key": "kb"},
    ]

    async def _servers_fn():
        return servers
    monkeypatch.setattr(radarr, "get_radarr_servers", _servers_fn)

    call_state = {"n": 0}

    def _make_client(*a, **kw):
        async def _get():
            call_state["n"] += 1
            if call_state["n"] == 1:
                return _FakeResp(200, [{"id": 1, "path": "/x", "movieFile": {"path": "/x/other.mkv"}}])
            return _FakeResp(200, [{"id": 42, "path": "/y", "movieFile": {"path": "/y/target.mkv"}}])
        return _FakeClient(_get)

    monkeypatch.setattr(httpx, "AsyncClient", _make_client)

    result = await radarr.radarr_find_by_path("/y/target.mkv")
    assert result == (42, "http://b:1", "kb")


async def test_find_by_path_skips_non_200_server(monkeypatch):
    servers = [{"url": "http://a:1", "api_key": "ka"}]

    async def _servers_fn():
        return servers
    monkeypatch.setattr(radarr, "get_radarr_servers", _servers_fn)

    def _make_client(*a, **kw):
        async def _get():
            return _FakeResp(500, [])
        return _FakeClient(_get)
    monkeypatch.setattr(httpx, "AsyncClient", _make_client)

    assert await radarr.radarr_find_by_path("/x/x.mkv") is None


async def test_find_by_path_swallows_exception_and_continues(monkeypatch):
    servers = [{"url": "http://a:1", "api_key": "ka"}]

    async def _servers_fn():
        return servers
    monkeypatch.setattr(radarr, "get_radarr_servers", _servers_fn)

    def _make_client(*a, **kw):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(httpx, "AsyncClient", _make_client)

    assert await radarr.radarr_find_by_path("/x/x.mkv") is None


# ─── radarr_get ─────────────────────────────────────────────────────────────────

async def test_radarr_get_returns_none_without_creds(monkeypatch):
    async def _config():
        return "", ""
    monkeypatch.setattr(radarr, "_radarr_config", _config)
    assert await radarr.radarr_get(1) is None


async def test_radarr_get_returns_none_without_id():
    assert await radarr.radarr_get(0) is None


async def test_radarr_get_returns_movie_on_200(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{RADARR_URL}/api/v3/movie/42", json={"id": 42, "title": "Dune"}
    )
    result = await radarr.radarr_get(42)
    assert result == {"id": 42, "title": "Dune"}


async def test_radarr_get_returns_none_on_non_200(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{RADARR_URL}/api/v3/movie/42", status_code=404)
    assert await radarr.radarr_get(42) is None


async def test_radarr_get_returns_none_on_exception(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(httpx.ConnectError("down"))
    assert await radarr.radarr_get(42) is None


# ─── radarr_get_poster_url ──────────────────────────────────────────────────────

async def test_get_poster_url_returns_empty_when_movie_not_found(monkeypatch):
    async def _get(radarr_id):
        return None
    monkeypatch.setattr(radarr, "radarr_get", _get)
    assert await radarr.radarr_get_poster_url(1) == ""


async def test_get_poster_url_extracts_from_images(monkeypatch):
    async def _get(radarr_id):
        return {"images": [{"coverType": "poster", "remoteUrl": "http://cdn/p.jpg"}]}
    monkeypatch.setattr(radarr, "radarr_get", _get)
    assert await radarr.radarr_get_poster_url(1) == "http://cdn/p.jpg"


# ─── radarr_delete — CRITICAL: exact target + deleteFiles ──────────────────────

async def test_delete_sends_exact_id_and_delete_files_true(httpx_mock: HTTPXMock):
    httpx_mock.add_response(status_code=200)
    ok = await radarr.radarr_delete(4242, delete_files=True)
    assert ok is True
    req = httpx_mock.get_requests()[0]
    assert req.url.path == "/api/v3/movie/4242"
    assert dict(req.url.params)["deleteFiles"] == "true"


async def test_delete_sends_delete_files_false_when_keeping_files(httpx_mock: HTTPXMock):
    httpx_mock.add_response(status_code=200)
    await radarr.radarr_delete(4242, delete_files=False)
    req = httpx_mock.get_requests()[0]
    assert dict(req.url.params)["deleteFiles"] == "false"


async def test_delete_returns_false_without_id_or_creds(monkeypatch):
    assert await radarr.radarr_delete(0) is False
    async def _config():
        return "", ""
    monkeypatch.setattr(radarr, "_radarr_config", _config)
    assert await radarr.radarr_delete(1) is False


async def test_delete_returns_false_on_204_and_200_only(httpx_mock: HTTPXMock):
    httpx_mock.add_response(status_code=404)
    assert await radarr.radarr_delete(1) is False


async def test_delete_returns_false_on_exception(monkeypatch):
    async def _with_retry(fn, *a, **kw):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(radarr, "with_retry", _with_retry)
    assert await radarr.radarr_delete(1) is False


# ─── radarr_get_torrent_hash ─────────────────────────────────────────────────────

async def test_get_torrent_hash_returns_none_without_creds(monkeypatch):
    async def _config():
        return "", ""
    monkeypatch.setattr(radarr, "_radarr_config", _config)
    assert await radarr.radarr_get_torrent_hash(1) is None


async def test_get_torrent_hash_found_in_movie_history(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{RADARR_URL}/api/v3/history/movie?movieId=42",
        json=[{"downloadId": "AABBCCDDEEFF00112233445566778899AABBCC"}],
    )
    result = await radarr.radarr_get_torrent_hash(42)
    assert result == "aabbccddeeff00112233445566778899aabbcc"


async def test_get_torrent_hash_falls_back_to_general_history(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{RADARR_URL}/api/v3/history/movie?movieId=42", json=[]
    )
    httpx_mock.add_response(
        url=f"{RADARR_URL}/api/v3/history?movieId=42&pageSize=20",
        json={"records": [{"downloadId": "11223344556677889900AABBCCDDEEFF00112233"}]},
    )
    result = await radarr.radarr_get_torrent_hash(42)
    assert result == "11223344556677889900aabbccddeeff00112233"


async def test_get_torrent_hash_returns_none_when_no_history(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{RADARR_URL}/api/v3/history/movie?movieId=42", json=[])
    httpx_mock.add_response(
        url=f"{RADARR_URL}/api/v3/history?movieId=42&pageSize=20", json={"records": []}
    )
    assert await radarr.radarr_get_torrent_hash(42) is None


async def test_get_torrent_hash_returns_none_on_exception(monkeypatch):
    async def _with_retry(fn, *a, **kw):
        raise httpx.ReadTimeout("timeout")
    monkeypatch.setattr(radarr, "with_retry", _with_retry)
    assert await radarr.radarr_get_torrent_hash(1) is None


# ─── radarr_delete_by_id — CRITICAL: never guesses the wrong server ────────────

async def test_delete_by_id_resolves_via_arr_server_url_and_forwards_params(monkeypatch):
    servers = [
        {"url": "http://a:1", "api_key": "ka"},
        {"url": "http://b:1", "api_key": "kb"},
    ]
    async def _servers_fn():
        return servers
    monkeypatch.setattr(radarr, "get_radarr_servers", _servers_fn)

    captured = {}

    async def _delete(radarr_id, delete_files=False, url="", key=""):
        captured.update(radarr_id=radarr_id, delete_files=delete_files, url=url, key=key)
        return True
    monkeypatch.setattr(radarr, "radarr_delete", _delete)

    ok = await radarr.radarr_delete_by_id(99, delete_files=True, arr_server_url="http://b:1")
    assert ok is True
    assert captured == {"radarr_id": 99, "delete_files": True, "url": "http://b:1", "key": "kb"}


async def test_delete_by_id_refuses_when_server_unresolvable(monkeypatch):
    """arr_server_url points at a server no longer configured, no file_path
    fallback available -> must refuse (return False), never guess."""
    async def _servers_fn():
        return [{"url": "http://a:1", "api_key": "ka"}]
    monkeypatch.setattr(radarr, "get_radarr_servers", _servers_fn)

    async def _delete(*a, **kw):
        raise AssertionError("must never call radarr_delete when target is unresolved")
    monkeypatch.setattr(radarr, "radarr_delete", _delete)

    ok = await radarr.radarr_delete_by_id(99, arr_server_url="http://gone:1")
    assert ok is False


async def test_delete_by_id_falls_back_to_file_path_match(monkeypatch):
    async def _servers_fn():
        return [{"url": "http://a:1", "api_key": "ka"}, {"url": "http://b:1", "api_key": "kb"}]
    monkeypatch.setattr(radarr, "get_radarr_servers", _servers_fn)

    async def _find(path):
        return (99, "http://b:1", "kb")
    monkeypatch.setattr(radarr, "radarr_find_by_path", _find)

    captured = {}
    async def _delete(radarr_id, delete_files=False, url="", key=""):
        captured.update(url=url, key=key)
        return True
    monkeypatch.setattr(radarr, "radarr_delete", _delete)

    ok = await radarr.radarr_delete_by_id(99, arr_server_url=None, file_path="/data/x.mkv")
    assert ok is True
    assert captured == {"url": "http://b:1", "key": "kb"}


# ─── radarr_get_any / radarr_get_torrent_hash_any ──────────────────────────────

async def test_get_any_returns_first_server_with_match(monkeypatch):
    async def _servers_fn():
        return [{"url": "http://a:1", "api_key": "ka"}, {"url": "http://b:1", "api_key": "kb"}]
    monkeypatch.setattr(radarr, "get_radarr_servers", _servers_fn)

    async def _get(radarr_id, url="", key=""):
        return {"found_on": url} if url == "http://b:1" else None
    monkeypatch.setattr(radarr, "radarr_get", _get)

    result = await radarr.radarr_get_any(1)
    assert result == {"found_on": "http://b:1"}


async def test_get_torrent_hash_any_returns_first_match(monkeypatch):
    async def _servers_fn():
        return [{"url": "http://a:1", "api_key": "ka"}]
    monkeypatch.setattr(radarr, "get_radarr_servers", _servers_fn)

    async def _hash(radarr_id, url="", key=""):
        return "deadbeef"
    monkeypatch.setattr(radarr, "radarr_get_torrent_hash", _hash)

    assert await radarr.radarr_get_torrent_hash_any(1) == "deadbeef"
