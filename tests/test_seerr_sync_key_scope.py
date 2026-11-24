"""4.6.0 (P4-11, P4-12/D6): Seerr sync key scope, manual same-host redirects, masked response."""
import json

import httpx
import pytest
import respx

import backend.services.arr_service as svc

SEERR = "http://seerr.test"
EVIL = "http://evil.test"


@pytest.fixture
def settings(monkeypatch):
    store: dict[str, str] = {"seerr_url": SEERR, "seerr_api_key": "stored-key",
                             "radarr_servers": "[]", "sonarr_servers": "[]"}

    async def _get(key, default=None):
        return store.get(key, default)

    async def _set(key, value):
        store[key] = value

    async def _no_block(url):
        return ""

    async def _no_hook(request):
        return None

    monkeypatch.setattr(svc, "get_setting", _get)
    monkeypatch.setattr(svc, "set_setting", _set)
    monkeypatch.setattr(svc, "_reject_if_loopback_or_link_local", _no_block)
    monkeypatch.setattr(svc, "_ssrf_guard_hook", _no_hook)
    return store


def _mock_empty(router, base):
    for ep in ("settings/radarr", "service/radarr", "settings/sonarr", "service/sonarr"):
        router.get(f"{base}/api/v1/{ep}").mock(return_value=httpx.Response(200, json=[]))


@pytest.mark.asyncio
async def test_stored_key_not_sent_to_other_url(settings):
    with respx.mock(assert_all_called=False) as m:
        evil = m.get(url__regex=r"http://evil\.test/.*").mock(return_value=httpx.Response(200, json=[]))
        with pytest.raises(ValueError, match="Ressaisissez la clé API"):
            await svc.sync_arr_from_seerr(EVIL, "***")
        with pytest.raises(ValueError, match="Ressaisissez la clé API"):
            await svc.sync_arr_from_seerr(EVIL, "")
        assert not evil.called
        assert len(m.calls) == 0


@pytest.mark.asyncio
async def test_stored_key_reused_for_registered_url_after_normalisation(settings):
    with respx.mock(assert_all_called=False) as m:
        _mock_empty(m, SEERR)
        await svc.sync_arr_from_seerr("HTTP://Seerr.test/", "***")
        assert m.calls[0].request.headers["X-Api-Key"] == "stored-key"


@pytest.mark.asyncio
async def test_stored_key_not_reused_when_scheme_differs(settings):
    with respx.mock(assert_all_called=False) as m:
        with pytest.raises(ValueError, match="Ressaisissez"):
            await svc.sync_arr_from_seerr("https://seerr.test", "***")
        assert len(m.calls) == 0


@pytest.mark.asyncio
async def test_cross_host_redirect_drops_key(settings):
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{SEERR}/api/v1/settings/radarr").mock(
            return_value=httpx.Response(302, headers={"Location": f"{EVIL}/steal"}))
        evil = m.get(url__regex=r"http://evil\.test/.*").mock(return_value=httpx.Response(200, json=[]))
        with pytest.raises(ValueError, match="autre hôte"):
            await svc.sync_arr_from_seerr(SEERR, "typed-key")
        assert not evil.called
        assert all(c.request.url.host == "seerr.test" for c in m.calls)


@pytest.mark.asyncio
async def test_same_host_http_to_https_redirect_is_followed_with_key(settings):
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{SEERR}/api/v1/settings/radarr").mock(
            return_value=httpx.Response(301, headers={"Location": "https://seerr.test/api/v1/settings/radarr"}))
        secure = m.get("https://seerr.test/api/v1/settings/radarr").mock(return_value=httpx.Response(
            200, json=[{"id": 1, "name": "R", "hostname": "radarr", "port": 7878, "apiKey": "a"}]))
        m.get(url__regex=r".*/api/v1/(service/radarr|settings/sonarr|service/sonarr)").mock(
            return_value=httpx.Response(200, json=[]))
        result = await svc.sync_arr_from_seerr(SEERR, "typed-key")
        assert secure.calls[0].request.headers["X-Api-Key"] == "typed-key"
        assert result["message"].startswith("1 Radarr")


@pytest.mark.asyncio
async def test_https_to_http_downgrade_redirect_refused(settings):
    settings["seerr_url"] = "https://seerr.test"
    with respx.mock(assert_all_called=False) as m:
        m.get("https://seerr.test/api/v1/settings/radarr").mock(
            return_value=httpx.Response(302, headers={"Location": "http://seerr.test/api/v1/settings/radarr"}))
        plain = m.get("http://seerr.test/api/v1/settings/radarr").mock(return_value=httpx.Response(200, json=[]))
        with pytest.raises(ValueError):
            await svc.sync_arr_from_seerr("https://seerr.test", "k")
        assert not plain.called


@pytest.mark.asyncio
async def test_redirect_loop_is_bounded(settings):
    with respx.mock(assert_all_called=False) as m:
        route = m.get(f"{SEERR}/api/v1/settings/radarr").mock(
            return_value=httpx.Response(302, headers={"Location": f"{SEERR}/api/v1/settings/radarr"}))
        with pytest.raises(ValueError, match="redirections"):
            await svc.sync_arr_from_seerr(SEERR, "k")
        assert route.call_count <= 5


@pytest.mark.asyncio
async def test_every_redirect_hop_is_ssrf_revalidated(settings, monkeypatch):
    async def _block_second(url):
        return "Hôte non autorisé : seerr.test" if "/hop2" in url else ""

    monkeypatch.setattr(svc, "_reject_if_loopback_or_link_local", _block_second)
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{SEERR}/api/v1/settings/radarr").mock(
            return_value=httpx.Response(302, headers={"Location": f"{SEERR}/hop2"}))
        hop2 = m.get(f"{SEERR}/hop2").mock(return_value=httpx.Response(200, json=[]))
        with pytest.raises(ValueError, match="non autorisé"):
            await svc.sync_arr_from_seerr(SEERR, "k")
        assert not hop2.called


@pytest.mark.asyncio
async def test_sync_response_masks_keys_but_persists_real_ones(settings):
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{SEERR}/api/v1/settings/radarr").mock(return_value=httpx.Response(
            200, json=[{"id": 1, "name": "R", "hostname": "radarr", "port": 7878, "apiKey": "radarr-secret"}]))
        m.get(f"{SEERR}/api/v1/settings/sonarr").mock(return_value=httpx.Response(
            200, json=[{"id": 2, "name": "S", "hostname": "sonarr", "port": 8989, "apiKey": "sonarr-secret"}]))
        result = await svc.sync_arr_from_seerr(SEERR, "k")
    assert "radarr-secret" not in json.dumps(result) and "sonarr-secret" not in json.dumps(result)
    assert result["radarr_servers"][0]["api_key"] == "***"
    assert result["sonarr_servers"][0]["api_key"] == "***"
    assert json.loads(settings["radarr_servers"])[0]["api_key"] == "radarr-secret"
    assert json.loads(settings["sonarr_servers"])[0]["api_key"] == "sonarr-secret"
