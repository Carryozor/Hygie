"""arr_service edge cases found during the 2026-09-29 coverage pass."""
import json

import pytest

import backend.services.arr_service as svc


@pytest.fixture
def settings(monkeypatch):
    store: dict[str, str] = {}

    async def _get(key, default=None):
        return store.get(key, default)

    async def _set(key, value):
        store[key] = value

    async def _no_block(url):
        return None

    async def _no_hook(request):
        return None

    monkeypatch.setattr(svc, "get_setting", _get)
    monkeypatch.setattr(svc, "set_setting", _set)
    monkeypatch.setattr(svc, "_reject_if_loopback_or_link_local", _no_block)
    monkeypatch.setattr(svc, "_ssrf_guard_hook", _no_hook)
    return store


async def test_masked_key_with_no_stored_match_is_rejected_without_calling_the_server(settings, httpx_mock):
    """The '***' placeholder must never be sent as X-Api-Key."""
    settings["radarr_servers"] = json.dumps([{"url": "http://other:7878", "api_key": "real"}])

    result = await svc.test_arr_instance("radarr", "http://radarr:7878", "***")

    assert result == {"ok": False, "message": "URL et clé API requis"}
    assert httpx_mock.get_requests() == []


async def test_seerr_sync_saves_and_reports_when_stored_servers_json_is_corrupt(settings, httpx_mock):
    settings["seerr_api_key"] = "k"
    settings["radarr_servers"] = "{not valid json"
    settings["sonarr_servers"] = "[]"
    httpx_mock.add_response(url="http://seerr.test/api/v1/settings/radarr",
                            json=[{"id": 1, "name": "R", "hostname": "radarr", "port": 7878, "apiKey": "a"}])
    httpx_mock.add_response(url="http://seerr.test/api/v1/settings/sonarr", json=[])
    httpx_mock.add_response(url="http://seerr.test/api/v1/service/sonarr", json=[])

    result = await svc.sync_arr_from_seerr("http://seerr.test", "***")

    assert len(json.loads(settings["radarr_servers"])) == 1
    assert result["message"] == "1 Radarr et 0 Sonarr ajouté(s)"
