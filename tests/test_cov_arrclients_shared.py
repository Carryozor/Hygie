"""Coverage tests for backend/arr_clients/shared.py — helpers shared by the
Radarr/Sonarr/Seerr clients. These are load-bearing for delete-target
resolution (_resolve_arr_server, _get_arr_servers): a wrong result here
means a delete lands on the wrong server or id.
"""
import os

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")

from pytest_httpx import HTTPXMock

from backend.arr_clients.shared import (
    _extract_poster_url,
    _first_from_servers,
    _get_arr_servers,
    _normalize_arr_url,
    _path_matches,
    _resolve_arr_creds,
    _resolve_arr_server,
    _test_arr_connection,
)


# ─── _resolve_arr_creds ────────────────────────────────────────────────────────

async def test_resolve_arr_creds_uses_explicit_url_and_key_when_given():
    async def _config():
        raise AssertionError("config_fn must not be called when url/key are given")

    url, key = await _resolve_arr_creds("http://explicit:1", "explicit-key", _config)
    assert (url, key) == ("http://explicit:1", "explicit-key")


async def test_resolve_arr_creds_falls_back_to_config_fn_when_missing():
    async def _config():
        return "http://default:2", "default-key"

    url, key = await _resolve_arr_creds("", "", _config)
    assert (url, key) == ("http://default:2", "default-key")


async def test_resolve_arr_creds_falls_back_when_only_key_missing():
    async def _config():
        return "http://default:2", "default-key"

    url, key = await _resolve_arr_creds("http://explicit:1", "", _config)
    assert (url, key) == ("http://default:2", "default-key")


# ─── _extract_poster_url ───────────────────────────────────────────────────────

def test_extract_poster_url_returns_first_public_poster():
    images = [
        {"coverType": "fanart", "remoteUrl": "http://cdn/fanart.jpg"},
        {"coverType": "poster", "remoteUrl": "http://cdn/poster1.jpg"},
        {"coverType": "poster", "remoteUrl": "http://cdn/poster2.jpg"},
    ]
    assert _extract_poster_url(images) == "http://cdn/poster1.jpg"


def test_extract_poster_url_skips_non_http_remote_url():
    images = [{"coverType": "poster", "remoteUrl": "/local/path/poster.jpg"}]
    assert _extract_poster_url(images) == ""


def test_extract_poster_url_returns_empty_when_no_poster():
    assert _extract_poster_url([{"coverType": "fanart", "remoteUrl": "http://cdn/x.jpg"}]) == ""
    assert _extract_poster_url([]) == ""


# ─── _test_arr_connection ──────────────────────────────────────────────────────

async def test_test_arr_connection_reports_not_configured_when_missing_creds():
    ok, msg = await _test_arr_connection("", "", "Radarr")
    assert ok is False
    assert msg == "Non configuré"


async def test_test_arr_connection_success_reports_version(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="http://radarr:1/api/v3/system/status", json={"version": "5.0.0"}
    )
    ok, msg = await _test_arr_connection("http://radarr:1", "k", "Radarr")
    assert ok is True
    assert msg == "Radarr 5.0.0"


async def test_test_arr_connection_reports_http_error_status(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="http://radarr:1/api/v3/system/status", status_code=401)
    ok, msg = await _test_arr_connection("http://radarr:1", "k", "Radarr")
    assert ok is False
    assert msg == "HTTP 401"


async def test_test_arr_connection_reports_network_exception(httpx_mock: HTTPXMock):
    import httpx
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    ok, msg = await _test_arr_connection("http://radarr:1", "k", "Radarr")
    assert ok is False
    assert "refused" in msg


# ─── _normalize_arr_url ────────────────────────────────────────────────────────

def test_normalize_arr_url_strips_trailing_slash_and_lowercases():
    assert _normalize_arr_url("HTTP://Radarr.Local:7878/") == "http://radarr.local:7878"


def test_normalize_arr_url_handles_none_and_empty():
    assert _normalize_arr_url(None) == ""
    assert _normalize_arr_url("") == ""


# ─── _resolve_arr_server — critical for not deleting from the wrong server ────

async def test_resolve_arr_server_matches_exact_normalized_url():
    servers = [
        {"url": "http://radarr-a:7878/", "api_key": "key-a"},
        {"url": "http://radarr-b:7878/", "api_key": "key-b"},
    ]
    result = await _resolve_arr_server(servers, "HTTP://radarr-b:7878")
    assert result == ("http://radarr-b:7878", "key-b")


async def test_resolve_arr_server_returns_none_when_recorded_server_gone():
    """arr_server_url was recorded at scan time but no longer matches any
    configured server (removed/reconfigured) — must refuse, never guess."""
    servers = [{"url": "http://radarr-a:7878", "api_key": "key-a"}]
    result = await _resolve_arr_server(servers, "http://radarr-removed:7878")
    assert result is None


async def test_resolve_arr_server_legacy_row_resolves_with_single_server():
    servers = [{"url": "http://only-server:7878", "api_key": "key-only"}]
    result = await _resolve_arr_server(servers, None)
    assert result == ("http://only-server:7878", "key-only")


async def test_resolve_arr_server_legacy_row_refuses_with_multiple_servers():
    """A legacy row (no arr_server_url) is ambiguous when 2+ servers are
    configured — must refuse rather than pick one arbitrarily."""
    servers = [
        {"url": "http://a:7878", "api_key": "key-a"},
        {"url": "http://b:7878", "api_key": "key-b"},
    ]
    result = await _resolve_arr_server(servers, None)
    assert result is None


async def test_resolve_arr_server_legacy_row_refuses_with_zero_servers():
    result = await _resolve_arr_server([], None)
    assert result is None


# ─── _first_from_servers ───────────────────────────────────────────────────────

async def test_first_from_servers_returns_first_truthy_result():
    servers = [
        {"url": "http://a:1", "api_key": "ka"},
        {"url": "http://b:1", "api_key": "kb"},
    ]
    calls = []

    async def _fetch(url, key):
        calls.append(url)
        if url == "http://a:1":
            return None
        return {"found": url}

    result = await _first_from_servers(servers, _fetch)
    assert result == {"found": "http://b:1"}
    assert calls == ["http://a:1", "http://b:1"]


async def test_first_from_servers_returns_none_when_no_match():
    servers = [{"url": "http://a:1", "api_key": "ka"}]

    async def _fetch(url, key):
        return None

    assert await _first_from_servers(servers, _fetch) is None


async def test_first_from_servers_empty_list_returns_none():
    async def _fetch(url, key):
        raise AssertionError("must not be called for an empty server list")

    assert await _first_from_servers([], _fetch) is None


# ─── _get_arr_servers ──────────────────────────────────────────────────────────

async def test_get_arr_servers_parses_multi_server_setting(monkeypatch):
    import backend.arr_clients.shared as shared_mod

    async def _get_setting(key):
        import json
        if key == "radarr_servers":
            return json.dumps([
                {"id": "1", "name": "A", "url": "http://a:1", "api_key": "ka", "enabled": True},
                {"id": "2", "name": "B", "url": "http://b:1", "api_key": "kb", "enabled": False},
            ])
        return None

    monkeypatch.setattr(shared_mod, "get_setting", _get_setting)

    async def _legacy():
        raise AssertionError("legacy fallback must not run when multi-server setting is present")

    servers = await _get_arr_servers("radarr_servers", _legacy, "Radarr")
    assert len(servers) == 1
    assert servers[0]["url"] == "http://a:1"


async def test_get_arr_servers_falls_back_to_legacy_when_no_multi_setting(monkeypatch):
    import backend.arr_clients.shared as shared_mod

    async def _get_setting(key):
        return None

    monkeypatch.setattr(shared_mod, "get_setting", _get_setting)

    async def _legacy():
        return "http://legacy:7878", "legacy-key"

    servers = await _get_arr_servers("radarr_servers", _legacy, "Radarr")
    assert servers == [{
        "id": "legacy", "name": "Radarr",
        "url": "http://legacy:7878", "api_key": "legacy-key", "enabled": True,
    }]


async def test_get_arr_servers_returns_empty_when_neither_configured(monkeypatch):
    import backend.arr_clients.shared as shared_mod

    async def _get_setting(key):
        return None

    monkeypatch.setattr(shared_mod, "get_setting", _get_setting)

    async def _legacy():
        return "", ""

    assert await _get_arr_servers("radarr_servers", _legacy, "Radarr") == []


async def test_get_arr_servers_malformed_json_falls_back_to_legacy(monkeypatch):
    import backend.arr_clients.shared as shared_mod

    async def _get_setting(key):
        return "not-json-{{"

    monkeypatch.setattr(shared_mod, "get_setting", _get_setting)

    async def _legacy():
        return "http://legacy:1", "legacy-key"

    servers = await _get_arr_servers("radarr_servers", _legacy, "Radarr")
    assert servers[0]["url"] == "http://legacy:1"


# ─── _path_matches ─────────────────────────────────────────────────────────────

def test_path_matches_exact_file_path():
    assert _path_matches("/data/movies/x/x.mkv", "/data/movies/x/x.mkv", "") is True


def test_path_matches_file_inside_folder():
    assert _path_matches("/data/movies/x/x.mkv", "", "/data/movies/x") is True


def test_path_matches_false_when_neither_matches():
    assert _path_matches("/data/movies/y/y.mkv", "/data/movies/x/x.mkv", "/data/movies/x") is False


def test_path_matches_false_for_sibling_folder_prefix_collision():
    """A folder name that is a string-prefix of another must not match —
    matching requires the '/' boundary, not bare startswith."""
    assert _path_matches("/data/movies/xyz/x.mkv", "", "/data/movies/x") is False
