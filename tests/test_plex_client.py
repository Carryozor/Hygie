# tests/test_plex_client.py
"""Unit tests for PlexClient using respx to mock HTTP calls."""
import pytest
import respx
import httpx

PLEX_URL = "http://plex.local:32400"
PLEX_TOKEN = "testtoken123"

LIBRARY_SECTIONS_RESP = {
    "MediaContainer": {
        "Directory": [
            {"key": "1", "title": "Movies", "type": "movie"},
            {"key": "2", "title": "TV Shows", "type": "show"},
        ]
    }
}

MOVIES_ALL_RESP = {
    "MediaContainer": {
        "Metadata": [
            {
                "ratingKey": "101",
                "title": "Inception",
                "type": "movie",
                "viewCount": 2,
                "lastViewedAt": 1700000000,
                "addedAt": 1600000000,
                "duration": 8880000,
                "rating": 8.5,
                "thumb": "/library/metadata/101/thumb/...",
                "Guid": [{"id": "tmdb://27205"}],
            }
        ]
    }
}

SESSION_RESP = {
    "MediaContainer": {
        "Metadata": [
            {
                "ratingKey": "101",
                "title": "Inception",
                "type": "movie",
                "User": {"id": "1", "title": "admin"},
                "viewOffset": 3600000,
            }
        ]
    }
}


@pytest.fixture
def plex():
    from backend.plex_client import PlexClient
    return PlexClient(url=PLEX_URL, token=PLEX_TOKEN)


@pytest.mark.asyncio
@respx.mock
async def test_get_libraries(plex):
    respx.get(f"{PLEX_URL}/library/sections").mock(
        return_value=httpx.Response(200, json=LIBRARY_SECTIONS_RESP,
                                    headers={"Content-Type": "application/json"})
    )
    libs = await plex.get_libraries()
    assert len(libs) == 2
    assert libs[0]["id"] == "1"
    assert libs[0]["title"] == "Movies"
    assert libs[0]["type"] == "movie"


@pytest.mark.asyncio
@respx.mock
async def test_scan_library(plex):
    respx.get(f"{PLEX_URL}/library/sections/1/all").mock(
        return_value=httpx.Response(200, json=MOVIES_ALL_RESP,
                                    headers={"Content-Type": "application/json"})
    )
    items = await plex.scan_library("1")
    assert len(items) == 1
    item = items[0]
    assert item["plex_id"] == "101"
    assert item["title"] == "Inception"
    assert item["media_type"] == "movie"
    assert item["view_count"] == 2
    assert item["last_viewed_at"] is not None
    assert item["tmdb_id"] == "27205"
    assert item["poster_url"].startswith("http")


@pytest.mark.asyncio
@respx.mock
async def test_delete_item(plex):
    respx.delete(f"{PLEX_URL}/library/metadata/101").mock(
        return_value=httpx.Response(200)
    )
    result = await plex.delete_item("101")
    assert result is True


@pytest.mark.asyncio
@respx.mock
async def test_delete_item_not_found(plex):
    respx.delete(f"{PLEX_URL}/library/metadata/999").mock(
        return_value=httpx.Response(404)
    )
    result = await plex.delete_item("999")
    assert result is False


@pytest.mark.asyncio
@respx.mock
async def test_get_active_sessions(plex):
    respx.get(f"{PLEX_URL}/status/sessions").mock(
        return_value=httpx.Response(200, json=SESSION_RESP,
                                    headers={"Content-Type": "application/json"})
    )
    sessions = await plex.get_active_sessions()
    assert len(sessions) == 1
    assert sessions[0]["plex_id"] == "101"
    assert sessions[0]["username"] == "admin"


@pytest.mark.asyncio
@respx.mock
async def test_get_item_metadata(plex):
    respx.get(f"{PLEX_URL}/library/metadata/101").mock(
        return_value=httpx.Response(
            200,
            json={"MediaContainer": {"Metadata": [MOVIES_ALL_RESP["MediaContainer"]["Metadata"][0]]}},
            headers={"Content-Type": "application/json"},
        )
    )
    meta = await plex.get_item_metadata("101")
    assert meta is not None
    assert meta["plex_id"] == "101"
    assert meta["title"] == "Inception"
