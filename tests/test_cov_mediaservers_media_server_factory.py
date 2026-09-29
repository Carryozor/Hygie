"""Coverage tests for backend/media_server_factory.py — the Plex/Emby dispatch
used by the deletion pipeline. A wrong dispatch here means either deleting
nothing (silently) or deleting via the wrong client/id.
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.media_server_factory import get_server_item_id, delete_server_item


# ─── get_server_item_id ────────────────────────────────────────────────────────

def test_get_server_item_id_uses_plex_rating_key_for_plex_server():
    server = {"type": "plex"}
    item = {"plex_rating_key": "999", "emby_id": "123"}
    assert get_server_item_id(server, item) == "999"


def test_get_server_item_id_falls_back_to_emby_id_when_plex_rating_key_absent():
    """Legacy rows created before plex_rating_key existed only have emby_id."""
    server = {"type": "plex"}
    item = {"emby_id": "legacy-42"}
    assert get_server_item_id(server, item) == "legacy-42"


def test_get_server_item_id_falls_back_to_emby_id_when_plex_rating_key_empty_string():
    server = {"type": "plex"}
    item = {"plex_rating_key": "", "emby_id": "legacy-42"}
    assert get_server_item_id(server, item) == "legacy-42"


def test_get_server_item_id_uses_emby_id_for_emby_server():
    server = {"type": "emby"}
    item = {"plex_rating_key": "999", "emby_id": "123"}
    assert get_server_item_id(server, item) == "123"


def test_get_server_item_id_returns_empty_string_when_no_emby_id():
    server = {"type": "emby"}
    assert get_server_item_id(server, {}) == ""


# ─── delete_server_item ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_server_item_plex_success_calls_plex_delete_with_rating_key():
    server = {"type": "plex", "id": "srv1"}
    item = {"plex_rating_key": "555", "emby_id": "123"}

    mock_plex = AsyncMock()
    mock_plex.delete_item.return_value = True
    with patch("backend.plex_client.build_plex_client", return_value=mock_plex):
        result = await delete_server_item(server, item)

    assert result is True
    mock_plex.delete_item.assert_awaited_once_with("555")


@pytest.mark.asyncio
async def test_delete_server_item_plex_client_unavailable_returns_false_without_deleting():
    """If we can't build a Plex client, we must report failure — never claim
    success for a deletion that never happened."""
    server = {"type": "plex", "id": "srv1"}
    item = {"plex_rating_key": "555"}

    with patch("backend.plex_client.build_plex_client", return_value=None):
        result = await delete_server_item(server, item)

    assert result is False


@pytest.mark.asyncio
async def test_delete_server_item_plex_failure_propagates_false():
    server = {"type": "plex", "id": "srv1"}
    item = {"plex_rating_key": "555"}

    mock_plex = AsyncMock()
    mock_plex.delete_item.return_value = False
    with patch("backend.plex_client.build_plex_client", return_value=mock_plex):
        result = await delete_server_item(server, item)

    assert result is False


@pytest.mark.asyncio
async def test_delete_server_item_emby_success_calls_emby_delete_item_with_emby_id():
    server = {"type": "emby", "id": "0"}
    item = {"emby_id": "777"}

    mock_delete = AsyncMock(return_value=True)
    with patch("backend.emby_client.delete_item", mock_delete):
        result = await delete_server_item(server, item)

    assert result is True
    mock_delete.assert_awaited_once_with("777", server_id="0")


@pytest.mark.asyncio
async def test_delete_server_item_emby_uses_explicit_server_id_override():
    """server_id kwarg must win over server['id'] when provided."""
    server = {"type": "emby", "id": "0"}
    item = {"emby_id": "777"}

    mock_delete = AsyncMock(return_value=True)
    with patch("backend.emby_client.delete_item", mock_delete):
        await delete_server_item(server, item, server_id="other-server")

    mock_delete.assert_awaited_once_with("777", server_id="other-server")


@pytest.mark.asyncio
async def test_delete_server_item_emby_failure_returns_false():
    server = {"type": "emby", "id": "0"}
    item = {"emby_id": "777"}

    mock_delete = AsyncMock(return_value=False)
    with patch("backend.emby_client.delete_item", mock_delete):
        result = await delete_server_item(server, item)

    assert result is False
