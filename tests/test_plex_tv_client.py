# tests/test_plex_tv_client.py
"""Unit tests for PlexTVClient using respx mocks."""
import pytest
import respx
import httpx

PLEX_TV_TOKEN = "mytv_token"
PLEX_TV_BASE = "https://plex.tv/api/v2"

FRIENDS_RESP = [
    {
        "id": 42,
        "username": "alice",
        "email": "alice@example.com",
        "thumb": "https://plex.tv/users/42/avatar",
        "title": "Alice",
    }
]

RESOURCES_RESP = [
    {
        "name": "My Plex Server",
        "clientIdentifier": "abc123",
        "product": "Plex Media Server",
        "provides": "server",
        "connections": [
            {"uri": "http://192.168.1.10:32400", "local": True, "relay": False},
            {"uri": "https://plex.direct:32400", "local": False, "relay": False},
        ],
    },
    {
        "name": "Plex Relay",
        "product": "Plex Media Server",
        "provides": "server",
        "connections": [
            {"uri": "https://relay.plex.direct", "local": False, "relay": True},
        ],
    },
]

USER_RESP = {"id": 1, "username": "admin", "email": "admin@example.com", "title": "Admin"}


@pytest.fixture
def plextv():
    from backend.plex_tv_client import PlexTVClient
    return PlexTVClient(token=PLEX_TV_TOKEN)


@pytest.mark.asyncio
@respx.mock
async def test_get_user(plextv):
    respx.get(f"{PLEX_TV_BASE}/user").mock(
        return_value=httpx.Response(200, json=USER_RESP)
    )
    user = await plextv.get_user()
    assert user["username"] == "admin"
    assert user["id"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_get_friends(plextv):
    respx.get(f"{PLEX_TV_BASE}/friends").mock(
        return_value=httpx.Response(200, json=FRIENDS_RESP)
    )
    friends = await plextv.get_friends()
    assert len(friends) == 1
    assert friends[0]["username"] == "alice"
    assert friends[0]["plex_user_id"] == 42


@pytest.mark.asyncio
@respx.mock
async def test_get_servers(plextv):
    respx.get(f"{PLEX_TV_BASE}/resources").mock(
        return_value=httpx.Response(200, json=RESOURCES_RESP)
    )
    servers = await plextv.get_servers()
    assert len(servers) == 1
    assert servers[0]["name"] == "My Plex Server"
    assert servers[0]["identifier"] == "abc123"
    assert servers[0]["local_url"] == "http://192.168.1.10:32400"
    assert servers[0]["remote_url"] == "https://plex.direct:32400"


@pytest.mark.asyncio
@respx.mock
async def test_validate_token_ok(plextv):
    respx.get(f"{PLEX_TV_BASE}/user").mock(
        return_value=httpx.Response(200, json=USER_RESP)
    )
    ok = await plextv.validate_token()
    assert ok is True


@pytest.mark.asyncio
@respx.mock
async def test_validate_token_unauthorized(plextv):
    respx.get(f"{PLEX_TV_BASE}/user").mock(
        return_value=httpx.Response(401)
    )
    ok = await plextv.validate_token()
    assert ok is False
