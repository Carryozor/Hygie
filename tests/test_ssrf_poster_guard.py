"""SSRF guard for externally-influenced image (poster) fetches.

Poster URLs come from media metadata (Radarr/Sonarr/Emby/TMDB) and are followed
with redirects — a trusted host could 30x-redirect the fetch to 127.0.0.1 or the
cloud metadata endpoint. These tests lock in that every hop is re-validated.
"""
import os

import httpx
import pytest
import respx

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.pop("DATABASE_URL", None)

from backend.db.utils import ssrf_guard_hook, guarded_image_get


@pytest.mark.parametrize("host", ["127.0.0.1", "169.254.169.254"])
async def test_guard_blocks_loopback_and_link_local(host):
    req = httpx.Request("GET", f"http://{host}/poster.jpg")
    with pytest.raises(httpx.HTTPError):
        await ssrf_guard_hook(req)


@pytest.mark.parametrize("host", ["8.8.8.8", "192.168.1.50"])
async def test_guard_allows_public_and_lan(host):
    # RFC1918 LAN and public hosts are legitimate poster sources — must pass.
    req = httpx.Request("GET", f"http://{host}/poster.jpg")
    await ssrf_guard_hook(req)


@respx.mock
async def test_guarded_image_get_returns_image_bytes():
    respx.get("https://image.tmdb.org/poster.jpg").mock(
        return_value=httpx.Response(200, content=b"\xff\xd8img", headers={"content-type": "image/jpeg"})
    )
    assert await guarded_image_get("https://image.tmdb.org/poster.jpg") == b"\xff\xd8img"


@respx.mock
async def test_guarded_image_get_rejects_non_image():
    respx.get("https://host/notimage").mock(
        return_value=httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})
    )
    assert await guarded_image_get("https://host/notimage") is None


@respx.mock
async def test_guarded_image_get_blocks_redirect_to_loopback():
    # A whitelisted-looking CDN redirects to loopback — the guard must abort the
    # followed hop and yield None rather than fetching the internal target.
    respx.get("https://cdn.example/p.jpg").mock(
        return_value=httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
    )
    assert await guarded_image_get("https://cdn.example/p.jpg") is None
