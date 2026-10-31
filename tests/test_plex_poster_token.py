"""Plex poster URLs must not carry the Plex token (4.5).

`poster_url` is stored in the database and handed to the browser through the
image proxy; a `?X-Plex-Token=` in it leaked the admin's Plex token into the
queue table and into every API response listing the queue. The token now stays
server-side and the proxy adds it as a header, only toward the Plex server it
belongs to.
"""
import httpx
import pytest
import respx

from backend import proxy as proxy_mod
from backend.plex_client import PlexClient

TOKEN = "plex-secret-token"
PLEX = "http://plex.test:32400"


def test_normalized_item_poster_url_has_no_token():
    item = PlexClient(url=PLEX, token=TOKEN)._normalize_item(
        {"ratingKey": "1", "title": "T", "type": "movie", "thumb": "/library/metadata/1/thumb/9"})
    assert item["poster_url"] == f"{PLEX}/library/metadata/1/thumb/9"
    assert TOKEN not in item["poster_url"] and "X-Plex-Token" not in item["poster_url"]


SERVERS = [
    {"id": "p", "type": "plex", "url": PLEX, "api_key": TOKEN},
    {"id": "e", "type": "emby", "url": "http://emby.test:8096", "api_key": "emby-key"},
]


def test_plex_headers_only_for_a_matching_plex_server():
    assert proxy_mod._plex_headers(f"{PLEX}/library/metadata/1/thumb/9", SERVERS) == {"X-Plex-Token": TOKEN}
    assert proxy_mod._plex_headers("http://plex.test:9999/x", SERVERS) == {}          # other port
    assert proxy_mod._plex_headers("http://emby.test:8096/x", SERVERS) == {}          # not plex
    assert proxy_mod._plex_headers("http://evil.test:32400/x", SERVERS) == {}         # other host
    assert proxy_mod._plex_headers("not a url", SERVERS) == {}
    assert proxy_mod._plex_headers(f"{PLEX}/x", [{"type": "plex", "url": PLEX}]) == {}  # no token configured


class _Req:
    def __init__(self, url):
        self.query_params = {"url": url}
        self.headers = {}
        self.client = type("C", (), {"host": "10.1.1.1"})()


@pytest.fixture
def wired(monkeypatch):
    async def whitelist():
        return {("plex.test", 32400), ("other.test", 80)}

    async def not_loopback(host):
        return False

    async def servers():
        return SERVERS
    monkeypatch.setattr(proxy_mod, "_get_proxy_whitelist", whitelist)
    monkeypatch.setattr(proxy_mod, "is_loopback_or_link_local", not_loopback)
    monkeypatch.setattr(proxy_mod, "get_media_servers", servers)


@pytest.mark.asyncio
@respx.mock
async def test_proxy_sends_the_token_to_the_plex_server(wired):
    route = respx.get(f"{PLEX}/library/metadata/1/thumb/9").mock(
        return_value=httpx.Response(200, content=b"img", headers={"content-type": "image/jpeg"}))
    r = await proxy_mod._fetch_proxied_image(_Req(f"{PLEX}/library/metadata/1/thumb/9"))
    assert r.status_code == 200
    assert route.calls.last.request.headers["X-Plex-Token"] == TOKEN


@pytest.mark.asyncio
@respx.mock
async def test_token_is_not_forwarded_on_a_redirect_to_another_host(wired):
    respx.get(f"{PLEX}/thumb").mock(return_value=httpx.Response(302, headers={"location": "http://other.test/img"}))
    other = respx.get("http://other.test/img").mock(
        return_value=httpx.Response(200, content=b"img", headers={"content-type": "image/png"}))
    r = await proxy_mod._fetch_proxied_image(_Req(f"{PLEX}/thumb"))
    assert r.status_code == 200
    assert "X-Plex-Token" not in other.calls.last.request.headers


@pytest.mark.asyncio
@respx.mock
async def test_legacy_url_with_token_in_query_still_works(wired):
    route = respx.get(f"{PLEX}/thumb").mock(
        return_value=httpx.Response(200, content=b"img", headers={"content-type": "image/jpeg"}))
    r = await proxy_mod._fetch_proxied_image(_Req(f"{PLEX}/thumb?X-Plex-Token={TOKEN}"))
    assert r.status_code == 200 and route.called
