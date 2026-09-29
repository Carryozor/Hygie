"""Coverage tests for backend/plex_client.py — parsing edge cases (missing
fields, dict-vs-list normalization), poster upload/restore success/failure,
and test_plex_server()/build_plex_client() branches.
"""
import respx
import httpx
import pytest

PLEX_URL = "http://plex.local:32400"
PLEX_TOKEN = "testtoken123"


@pytest.fixture
def plex():
    from backend.plex_client import PlexClient
    return PlexClient(url=PLEX_URL, token=PLEX_TOKEN)


# ─── _ts_to_iso ─────────────────────────────────────────────────────────────────

def test_ts_to_iso_returns_none_for_falsy_input():
    from backend.plex_client import _ts_to_iso
    assert _ts_to_iso(None) is None
    assert _ts_to_iso(0) is None


def test_ts_to_iso_converts_unix_timestamp():
    from backend.plex_client import _ts_to_iso
    result = _ts_to_iso(1700000000)
    assert result is not None
    assert result.startswith("2023-11-14")


# ─── _extract_tmdb_id ────────────────────────────────────────────────────────────

def test_extract_tmdb_id_returns_empty_string_for_no_guids():
    from backend.plex_client import _extract_tmdb_id
    assert _extract_tmdb_id([]) == ""
    assert _extract_tmdb_id(None) == ""


def test_extract_tmdb_id_ignores_non_tmdb_guids():
    from backend.plex_client import _extract_tmdb_id
    guids = [{"id": "imdb://tt1234567"}, {"id": "tvdb://999"}]
    assert _extract_tmdb_id(guids) == ""


def test_extract_tmdb_id_finds_tmdb_guid_among_others():
    from backend.plex_client import _extract_tmdb_id
    guids = [{"id": "imdb://tt1234567"}, {"id": "tmdb://27205"}]
    assert _extract_tmdb_id(guids) == "27205"


# ─── get_libraries: single-dict Directory normalization ─────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_libraries_normalizes_single_directory_dict_to_list(plex):
    respx.get(f"{PLEX_URL}/library/sections").mock(
        return_value=httpx.Response(
            200,
            json={"MediaContainer": {"Directory": {"key": "1", "title": "Movies", "type": "movie"}}},
            headers={"Content-Type": "application/json"},
        )
    )
    libs = await plex.get_libraries()
    assert libs == [{"id": "1", "title": "Movies", "type": "movie"}]


@pytest.mark.asyncio
@respx.mock
async def test_get_libraries_empty_when_no_directory_key(plex):
    respx.get(f"{PLEX_URL}/library/sections").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {}}, headers={"Content-Type": "application/json"})
    )
    assert await plex.get_libraries() == []


# ─── _normalize_item (via scan_library) — field defaults & edge cases ────────────

@pytest.mark.asyncio
@respx.mock
async def test_normalize_item_maps_show_type_to_series(plex):
    respx.get(f"{PLEX_URL}/library/sections/2/all").mock(
        return_value=httpx.Response(
            200,
            json={"MediaContainer": {"viewGroup": "show", "Metadata": [
                {"ratingKey": "5", "title": "A Show", "type": "show"},
            ]}},
            headers={"Content-Type": "application/json"},
        )
    )
    items = await plex.scan_library("2")
    assert items[0]["media_type"] == "series"


@pytest.mark.asyncio
@respx.mock
async def test_normalize_item_defaults_missing_fields_safely(plex):
    respx.get(f"{PLEX_URL}/library/sections/1/all").mock(
        return_value=httpx.Response(
            200,
            json={"MediaContainer": {"viewGroup": "", "Metadata": [{}]}},
            headers={"Content-Type": "application/json"},
        )
    )
    items = await plex.scan_library("1")
    item = items[0]
    assert item["plex_id"] == ""
    assert item["title"] == ""
    assert item["media_type"] == "movie"
    assert item["view_count"] == 0
    assert item["last_viewed_at"] is None
    assert item["poster_url"] == ""
    assert item["tmdb_id"] == ""
    assert item["season_number"] is None


@pytest.mark.asyncio
@respx.mock
async def test_normalize_item_wraps_single_guid_dict_as_list(plex):
    respx.get(f"{PLEX_URL}/library/sections/1/all").mock(
        return_value=httpx.Response(
            200,
            json={"MediaContainer": {"viewGroup": "", "Metadata": [
                {"ratingKey": "1", "Guid": {"id": "tmdb://555"}},
            ]}},
            headers={"Content-Type": "application/json"},
        )
    )
    items = await plex.scan_library("1")
    assert items[0]["tmdb_id"] == "555"


# ─── scan_library: movie-section re-fetch with type filter ──────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_scan_library_refetches_with_type_filter_for_movie_sections(plex):
    # respx's params matcher is a containment check, not an exact match, so a
    # route without `type` would also match the second (type=1) request —
    # use one route with side_effect responses returned in call order instead.
    route = respx.get(f"{PLEX_URL}/library/sections/1/all").mock(
        side_effect=[
            httpx.Response(200, json={"MediaContainer": {"viewGroup": "movie", "Metadata": []}},
                            headers={"Content-Type": "application/json"}),
            httpx.Response(
                200,
                json={"MediaContainer": {"viewGroup": "movie", "Metadata": [{"ratingKey": "9", "type": "movie"}]}},
                headers={"Content-Type": "application/json"},
            ),
        ]
    )
    items = await plex.scan_library("1")
    assert route.call_count == 2
    second_request = route.calls[1].request
    assert second_request.url.params["type"] == "1"
    assert len(items) == 1
    assert items[0]["plex_id"] == "9"


@pytest.mark.asyncio
@respx.mock
async def test_scan_library_normalizes_single_metadata_dict_to_list(plex):
    respx.get(f"{PLEX_URL}/library/sections/1/all").mock(
        return_value=httpx.Response(
            200,
            json={"MediaContainer": {"viewGroup": "show", "Metadata": {"ratingKey": "1", "type": "show"}}},
            headers={"Content-Type": "application/json"},
        )
    )
    items = await plex.scan_library("1")
    assert len(items) == 1
    assert items[0]["media_type"] == "series"


# ─── get_item_metadata ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_item_metadata_returns_none_on_404(plex):
    respx.get(f"{PLEX_URL}/library/metadata/999").mock(return_value=httpx.Response(404))
    assert await plex.get_item_metadata("999") is None


@pytest.mark.asyncio
@respx.mock
async def test_get_item_metadata_raises_on_non_404_http_error(plex):
    respx.get(f"{PLEX_URL}/library/metadata/1").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await plex.get_item_metadata("1")


@pytest.mark.asyncio
@respx.mock
async def test_get_item_metadata_returns_none_when_metadata_list_empty(plex):
    respx.get(f"{PLEX_URL}/library/metadata/1").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {}}, headers={"Content-Type": "application/json"})
    )
    assert await plex.get_item_metadata("1") is None


@pytest.mark.asyncio
@respx.mock
async def test_get_item_metadata_normalizes_single_dict_metadata(plex):
    respx.get(f"{PLEX_URL}/library/metadata/1").mock(
        return_value=httpx.Response(
            200,
            json={"MediaContainer": {"Metadata": {"ratingKey": "1", "title": "Solo"}}},
            headers={"Content-Type": "application/json"},
        )
    )
    meta = await plex.get_item_metadata("1")
    assert meta["title"] == "Solo"


# ─── delete_item: non-404 HTTP error propagates ──────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_delete_item_raises_on_server_error(plex):
    respx.delete(f"{PLEX_URL}/library/metadata/1").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await plex.delete_item("1")


# ─── upload_poster ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_upload_poster_success_returns_true(plex):
    respx.post(f"{PLEX_URL}/library/metadata/1/posters").mock(return_value=httpx.Response(200))
    assert await plex.upload_poster("1", b"image-bytes") is True


@pytest.mark.asyncio
@respx.mock
async def test_upload_poster_failure_returns_false_without_raising(plex):
    respx.post(f"{PLEX_URL}/library/metadata/1/posters").mock(return_value=httpx.Response(500))
    assert await plex.upload_poster("1", b"image-bytes") is False


# ─── restore_poster ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_restore_poster_success_returns_true(plex):
    respx.put(f"{PLEX_URL}/library/metadata/1/refresh").mock(return_value=httpx.Response(204))
    assert await plex.restore_poster("1") is True


@pytest.mark.asyncio
@respx.mock
async def test_restore_poster_failure_returns_false(plex):
    respx.put(f"{PLEX_URL}/library/metadata/1/refresh").mock(return_value=httpx.Response(500))
    assert await plex.restore_poster("1") is False


# ─── get_active_sessions ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_active_sessions_normalizes_single_metadata_dict(plex):
    respx.get(f"{PLEX_URL}/status/sessions").mock(
        return_value=httpx.Response(
            200,
            json={"MediaContainer": {"Metadata": {
                "ratingKey": "1", "title": "T", "type": "movie",
                "User": {"id": "9", "title": "bob"}, "viewOffset": 1000,
            }}},
            headers={"Content-Type": "application/json"},
        )
    )
    sessions = await plex.get_active_sessions()
    assert len(sessions) == 1
    assert sessions[0]["username"] == "bob"
    assert sessions[0]["view_offset_ms"] == 1000


@pytest.mark.asyncio
@respx.mock
async def test_get_active_sessions_empty_when_no_metadata(plex):
    respx.get(f"{PLEX_URL}/status/sessions").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {}}, headers={"Content-Type": "application/json"})
    )
    assert await plex.get_active_sessions() == []


# ─── get_recently_added ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_recently_added_passes_limit_and_normalizes_items(plex):
    route = respx.get(
        f"{PLEX_URL}/library/sections/1/recentlyAdded",
        params={"X-Plex-Container-Size": "10"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={"MediaContainer": {"Metadata": [{"ratingKey": "1", "title": "New Movie"}]}},
            headers={"Content-Type": "application/json"},
        )
    )
    items = await plex.get_recently_added("1", limit=10)
    assert route.called
    assert items[0]["title"] == "New Movie"


@pytest.mark.asyncio
@respx.mock
async def test_get_recently_added_normalizes_single_metadata_dict(plex):
    respx.get(f"{PLEX_URL}/library/sections/1/recentlyAdded").mock(
        return_value=httpx.Response(
            200,
            json={"MediaContainer": {"Metadata": {"ratingKey": "1", "title": "Solo Recent"}}},
            headers={"Content-Type": "application/json"},
        )
    )
    items = await plex.get_recently_added("1")
    assert len(items) == 1
    assert items[0]["title"] == "Solo Recent"


# ─── search ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_search_merges_results_across_multiple_hubs(plex):
    respx.get(f"{PLEX_URL}/hubs/search").mock(
        return_value=httpx.Response(
            200,
            json={"MediaContainer": {"Hub": [
                {"Metadata": [{"ratingKey": "1", "title": "Movie A"}]},
                {"Metadata": [{"ratingKey": "2", "title": "Movie B"}]},
            ]}},
            headers={"Content-Type": "application/json"},
        )
    )
    results = await plex.search("test query")
    assert {r["title"] for r in results} == {"Movie A", "Movie B"}


@pytest.mark.asyncio
@respx.mock
async def test_search_normalizes_single_hub_dict_and_single_metadata_dict(plex):
    respx.get(f"{PLEX_URL}/hubs/search").mock(
        return_value=httpx.Response(
            200,
            json={"MediaContainer": {"Hub": {"Metadata": {"ratingKey": "1", "title": "Solo Hit"}}}},
            headers={"Content-Type": "application/json"},
        )
    )
    results = await plex.search("q")
    assert len(results) == 1
    assert results[0]["title"] == "Solo Hit"


@pytest.mark.asyncio
@respx.mock
async def test_search_empty_when_no_hubs(plex):
    respx.get(f"{PLEX_URL}/hubs/search").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {}}, headers={"Content-Type": "application/json"})
    )
    assert await plex.search("q") == []


# ─── test_plex_server ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_test_plex_server_missing_credentials_returns_false():
    from backend.plex_client import test_plex_server
    ok, msg, server_type, *_ = await test_plex_server({"url": "", "api_key": ""})
    assert ok is False
    assert server_type == "plex"


@pytest.mark.asyncio
@respx.mock
async def test_test_plex_server_success_reports_version():
    from backend.plex_client import test_plex_server
    respx.get(f"{PLEX_URL}/identity").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"version": "1.32.5"}},
                                     headers={"Content-Type": "application/json"})
    )
    ok, msg, server_type, err = await test_plex_server({"url": PLEX_URL, "api_key": PLEX_TOKEN})
    assert ok is True
    assert "1.32.5" in msg
    assert err == ""


@pytest.mark.asyncio
@respx.mock
async def test_test_plex_server_http_error_maps_to_known_code():
    from backend.plex_client import test_plex_server
    respx.get(f"{PLEX_URL}/identity").mock(return_value=httpx.Response(401))
    ok, msg, server_type, err = await test_plex_server({"url": PLEX_URL, "api_key": "bad"})
    assert ok is False
    assert err == "http_401"


@pytest.mark.asyncio
@respx.mock
async def test_test_plex_server_http_error_falls_back_for_unknown_code():
    from backend.plex_client import test_plex_server
    respx.get(f"{PLEX_URL}/identity").mock(return_value=httpx.Response(418))
    ok, msg, server_type, err = await test_plex_server({"url": PLEX_URL, "api_key": "x"})
    assert err == "http_418"


@pytest.mark.asyncio
@respx.mock
async def test_test_plex_server_dns_failure_classified():
    from backend.plex_client import test_plex_server
    respx.get(f"{PLEX_URL}/identity").mock(side_effect=httpx.ConnectError("Name or service not known"))
    ok, msg, server_type, err = await test_plex_server({"url": PLEX_URL, "api_key": "x"})
    assert err == "dns_failure"


@pytest.mark.asyncio
@respx.mock
async def test_test_plex_server_connection_refused_classified():
    from backend.plex_client import test_plex_server
    respx.get(f"{PLEX_URL}/identity").mock(side_effect=httpx.ConnectError("Connection refused"))
    ok, msg, server_type, err = await test_plex_server({"url": PLEX_URL, "api_key": "x"})
    assert err == "connection_refused"


@pytest.mark.asyncio
@respx.mock
async def test_test_plex_server_timeout_classified():
    from backend.plex_client import test_plex_server
    respx.get(f"{PLEX_URL}/identity").mock(side_effect=httpx.TimeoutException("timed out"))
    ok, msg, server_type, err = await test_plex_server({"url": PLEX_URL, "api_key": "x"})
    assert err == "timeout"


@pytest.mark.asyncio
@respx.mock
async def test_test_plex_server_unknown_exception_classified_as_network_error():
    from backend.plex_client import test_plex_server
    respx.get(f"{PLEX_URL}/identity").mock(side_effect=RuntimeError("something weird"))
    ok, msg, server_type, err = await test_plex_server({"url": PLEX_URL, "api_key": "x"})
    assert err == "network_error"


# ─── build_plex_client ─────────────────────────────────────────────────────────────

def test_build_plex_client_returns_none_for_non_plex_type():
    from backend.plex_client import build_plex_client
    assert build_plex_client({"type": "emby", "url": PLEX_URL, "api_key": "x"}) is None


def test_build_plex_client_returns_none_when_url_missing():
    from backend.plex_client import build_plex_client
    assert build_plex_client({"type": "plex", "url": "", "api_key": "x"}) is None


def test_build_plex_client_returns_none_when_token_missing():
    from backend.plex_client import build_plex_client
    assert build_plex_client({"type": "plex", "url": PLEX_URL, "api_key": ""}) is None


def test_build_plex_client_returns_client_instance_on_success():
    from backend.plex_client import build_plex_client, PlexClient
    client = build_plex_client({"type": "plex", "id": "srv1", "url": PLEX_URL, "api_key": PLEX_TOKEN})
    assert isinstance(client, PlexClient)
