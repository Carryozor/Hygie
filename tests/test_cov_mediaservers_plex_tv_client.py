"""Coverage tests for backend/plex_tv_client.py — resource-list filtering
(product/provides mismatches must be excluded from discovered servers) and
validate_token's non-auth-error re-raise.
"""
import httpx
import pytest
import respx

PLEX_TV_BASE = "https://plex.tv/api/v2"


@pytest.fixture
def plextv():
    from backend.plex_tv_client import PlexTVClient
    return PlexTVClient(token="tok")


@pytest.mark.asyncio
@respx.mock
async def test_get_servers_skips_resources_with_non_plex_media_server_product(plextv):
    respx.get(f"{PLEX_TV_BASE}/resources").mock(
        return_value=httpx.Response(200, json=[
            {"name": "Some NAS", "product": "Plex Media Player", "provides": "server",
             "connections": [{"uri": "http://x:1", "local": True, "relay": False}]},
        ])
    )
    servers = await plextv.get_servers()
    assert servers == []


@pytest.mark.asyncio
@respx.mock
async def test_get_servers_skips_resources_not_providing_server(plextv):
    respx.get(f"{PLEX_TV_BASE}/resources").mock(
        return_value=httpx.Response(200, json=[
            {"name": "Some Player", "product": "Plex Media Server", "provides": "player",
             "connections": [{"uri": "http://x:1", "local": True, "relay": False}]},
        ])
    )
    servers = await plextv.get_servers()
    assert servers == []


@pytest.mark.asyncio
@respx.mock
async def test_validate_token_reraises_non_auth_http_error(plextv):
    respx.get(f"{PLEX_TV_BASE}/user").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await plextv.validate_token()
