"""Coverage for backend/services/arr_service.py business logic not exercised
by test_security_hardening.py (which already covers the SSRF-guard paths:
loopback/cloud-metadata blocking, LAN allowance, and redirect re-validation).

This file covers: masked-API-key resolution from stored servers, missing
input validation, non-200/exception handling in test_arr_instance(),
_build_arr_url(), and sync_arr_from_seerr()'s import/merge/dedup logic
(including the multi-endpoint fallback and "nothing found" outcome).
"""
import json

import httpx
import pytest
import respx

import backend.db.engine as _db_engine
import backend.db.schema as _db_schema
import backend.db.settings_store as _db_ss
import backend.db.utils as _db_utils

from backend.db.schema import init_db
from backend.db.settings_store import get_setting, set_setting


@pytest.fixture
async def fresh_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "arr_service.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    monkeypatch.setattr(_db_engine, "DIALECT", "sqlite")
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await init_db()
    yield db_path


# ─── test_arr_instance — input validation ──────────────────────────────────

async def test_test_arr_instance_rejects_missing_url_and_key(fresh_db):
    from backend.services.arr_service import test_arr_instance

    result = await test_arr_instance("radarr", "", "")
    assert result == {"ok": False, "message": "URL et clé API requis"}


async def test_test_arr_instance_rejects_missing_key_with_url_present(fresh_db):
    from backend.services.arr_service import test_arr_instance

    result = await test_arr_instance("radarr", "http://radarr.local:7878", "")
    assert result["ok"] is False


# ─── test_arr_instance — masked API key resolution ─────────────────────────

async def test_test_arr_instance_resolves_masked_key_from_stored_servers(fresh_db):
    """The UI sends back '***' for an already-saved server instead of the
    real key — the real key must be looked up by matching URL."""
    from backend.services.arr_service import test_arr_instance

    await set_setting("radarr_servers", json.dumps([
        {"url": "http://radarr.local:7878", "api_key": "real-secret-key", "enabled": True},
    ]))

    with respx.mock:
        route = respx.get("http://radarr.local:7878/api/v3/system/status").mock(
            return_value=httpx.Response(200, json={"version": "5.1.0"})
        )
        result = await test_arr_instance("radarr", "http://radarr.local:7878", "***")

    assert result == {"ok": True, "message": "Radarr 5.1.0"}
    assert route.calls[0].request.headers["X-Api-Key"] == "real-secret-key"


async def test_test_arr_instance_masked_key_without_stored_match_still_fails(fresh_db):
    """If no stored server matches the URL, the masked value can't be resolved
    to a real key.

    NOTE (see BUGS SUSPECTÉS in the final report): `key` stays equal to the
    literal "***" mask in this case, so the `if not url or not key:` guard
    does NOT catch it (a non-empty string is truthy) — the code goes on to
    send "***" itself as the X-Api-Key header instead of failing fast with
    "URL et clé API requis". The end result is still ok=False (connection/
    auth failure), so this only asserts the outward-visible contract, not the
    internal (buggy) message path.
    """
    from backend.services.arr_service import test_arr_instance

    await set_setting("radarr_servers", json.dumps([]))
    result = await test_arr_instance("radarr", "http://unknown-radarr:7878", "***")
    assert result["ok"] is False


# ─── test_arr_instance — HTTP outcomes ─────────────────────────────────────

async def test_test_arr_instance_reports_non_200_status(fresh_db):
    from backend.services.arr_service import test_arr_instance

    with respx.mock:
        respx.get("http://radarr.local:7878/api/v3/system/status").mock(
            return_value=httpx.Response(401)
        )
        result = await test_arr_instance("radarr", "http://radarr.local:7878", "bad-key")

    assert result == {"ok": False, "message": "HTTP 401"}


async def test_test_arr_instance_reports_connection_error(fresh_db):
    from backend.services.arr_service import test_arr_instance

    with respx.mock:
        respx.get("http://radarr.local:7878/api/v3/system/status").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        result = await test_arr_instance("radarr", "http://radarr.local:7878", "key")

    assert result["ok"] is False
    assert "connection refused" in result["message"]


async def test_test_arr_instance_labels_sonarr_correctly(fresh_db):
    from backend.services.arr_service import test_arr_instance

    with respx.mock:
        respx.get("http://sonarr.local:8989/api/v3/system/status").mock(
            return_value=httpx.Response(200, json={"version": "4.0.0"})
        )
        result = await test_arr_instance("sonarr", "http://sonarr.local:8989", "key")

    assert result == {"ok": True, "message": "Sonarr 4.0.0"}


# ─── _build_arr_url ─────────────────────────────────────────────────────────

def test_build_arr_url_https_with_base_url():
    from backend.services.arr_service import _build_arr_url

    url = _build_arr_url({"useSsl": True, "hostname": "radarr.example.com", "port": 443, "baseUrl": "/radarr/"})
    assert url == "https://radarr.example.com:443/radarr"


def test_build_arr_url_http_default_port_no_base_url():
    from backend.services.arr_service import _build_arr_url

    url = _build_arr_url({"hostname": "radarr.local", "port": 7878})
    assert url == "http://radarr.local:7878"


def test_build_arr_url_defaults_port_from_ssl_flag():
    """No explicit port given: 443 for https, 80 for http."""
    from backend.services.arr_service import _build_arr_url

    assert _build_arr_url({"useSsl": True, "hostname": "h"}) == "https://h:443"
    assert _build_arr_url({"useSsl": False, "hostname": "h"}) == "http://h:80"


# ─── sync_arr_from_seerr — input validation ────────────────────────────────

async def test_sync_arr_from_seerr_rejects_missing_url_or_key(fresh_db):
    from backend.services.arr_service import sync_arr_from_seerr

    with pytest.raises(ValueError):
        await sync_arr_from_seerr("", "key")
    with pytest.raises(ValueError):
        await sync_arr_from_seerr("http://seerr.local", "")


async def test_sync_arr_from_seerr_resolves_masked_seerr_key_from_settings(fresh_db):
    """A masked seerr_api_key falls back to the value already stored in
    settings, the same way test_arr_instance resolves masked arr keys."""
    from backend.services.arr_service import sync_arr_from_seerr

    await set_setting("seerr_api_key", "real-seerr-key")

    with respx.mock:
        route = respx.get("http://seerr.local/api/v1/settings/radarr").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.get("http://seerr.local/api/v1/service/radarr").mock(return_value=httpx.Response(404))
        respx.get("http://seerr.local/api/v1/settings/sonarr").mock(return_value=httpx.Response(200, json=[]))
        respx.get("http://seerr.local/api/v1/service/sonarr").mock(return_value=httpx.Response(404))
        await sync_arr_from_seerr("http://seerr.local", "***")

    assert route.calls[0].request.headers["X-Api-Key"] == "real-seerr-key"


# ─── sync_arr_from_seerr — import outcomes ─────────────────────────────────

async def test_sync_arr_from_seerr_reports_nothing_found(fresh_db):
    from backend.services.arr_service import sync_arr_from_seerr

    with respx.mock:
        respx.get("http://seerr.local/api/v1/settings/radarr").mock(return_value=httpx.Response(200, json=[]))
        respx.get("http://seerr.local/api/v1/service/radarr").mock(return_value=httpx.Response(404))
        respx.get("http://seerr.local/api/v1/settings/sonarr").mock(return_value=httpx.Response(200, json=[]))
        respx.get("http://seerr.local/api/v1/service/sonarr").mock(return_value=httpx.Response(404))
        result = await sync_arr_from_seerr("http://seerr.local", "key")

    assert result == {
        "radarr_servers": [], "sonarr_servers": [],
        "message": "Aucune instance trouvée dans Seerr",
    }


async def test_sync_arr_from_seerr_imports_and_reports_counts(fresh_db):
    from backend.services.arr_service import sync_arr_from_seerr

    with respx.mock:
        respx.get("http://seerr.local/api/v1/settings/radarr").mock(return_value=httpx.Response(
            200, json=[{"id": 1, "name": "Radarr HD", "hostname": "radarr", "port": 7878,
                        "useSsl": False, "baseUrl": "", "apiKey": "rkey"}]
        ))
        respx.get("http://seerr.local/api/v1/settings/sonarr").mock(return_value=httpx.Response(
            200, json=[{"id": 1, "name": "Sonarr HD", "hostname": "sonarr", "port": 8989,
                        "useSsl": False, "baseUrl": "", "apiKey": "skey"}]
        ))
        result = await sync_arr_from_seerr("http://seerr.local", "key")

    assert result["message"] == "1 Radarr et 1 Sonarr ajouté(s)"
    assert len(result["radarr_servers"]) == 1
    assert result["radarr_servers"][0]["id"] == "seerr-radarr-1"
    assert result["radarr_servers"][0]["url"] == "http://radarr:7878"
    assert len(result["sonarr_servers"]) == 1


async def test_sync_arr_from_seerr_falls_back_to_service_endpoint_when_settings_endpoint_empty(fresh_db):
    """Seerr exposes two possible endpoint shapes depending on version — the
    first non-matching (empty-list) response must not stop the search."""
    from backend.services.arr_service import sync_arr_from_seerr

    with respx.mock:
        respx.get("http://seerr.local/api/v1/settings/radarr").mock(return_value=httpx.Response(200, json=[]))
        respx.get("http://seerr.local/api/v1/service/radarr").mock(return_value=httpx.Response(
            200, json=[{"id": 2, "name": "Radarr 4K", "hostname": "radarr4k", "port": 7878,
                        "useSsl": False, "baseUrl": "", "apiKey": "rkey2"}]
        ))
        respx.get("http://seerr.local/api/v1/settings/sonarr").mock(return_value=httpx.Response(200, json=[]))
        respx.get("http://seerr.local/api/v1/service/sonarr").mock(return_value=httpx.Response(200, json=[]))
        result = await sync_arr_from_seerr("http://seerr.local", "key")

    assert len(result["radarr_servers"]) == 1
    assert result["radarr_servers"][0]["id"] == "seerr-radarr-2"


async def test_sync_arr_from_seerr_merge_deduplicates_by_url(fresh_db):
    """An instance already present (same URL) must not be duplicated when
    re-importing from Seerr — new, distinct servers are prepended instead."""
    from backend.services.arr_service import sync_arr_from_seerr

    existing = [{"id": "manual-1", "name": "Existing Radarr", "url": "http://radarr:7878/",
                 "api_key": "existing-key", "enabled": True}]
    await set_setting("radarr_servers", json.dumps(existing))

    with respx.mock:
        respx.get("http://seerr.local/api/v1/settings/radarr").mock(return_value=httpx.Response(
            200, json=[{"id": 1, "name": "Radarr (Seerr)", "hostname": "radarr", "port": 7878,
                        "useSsl": False, "baseUrl": "", "apiKey": "seerr-key"}]
        ))
        respx.get("http://seerr.local/api/v1/settings/sonarr").mock(return_value=httpx.Response(200, json=[]))
        respx.get("http://seerr.local/api/v1/service/sonarr").mock(return_value=httpx.Response(200, json=[]))
        result = await sync_arr_from_seerr("http://seerr.local", "key")

    assert len(result["radarr_servers"]) == 1, "the duplicate (same URL) must not be added again"
    assert result["radarr_servers"][0]["id"] == "manual-1", "the existing entry must be kept, not overwritten"
    assert result["message"] == "0 Radarr et 0 Sonarr ajouté(s)"

    saved = json.loads(await get_setting("radarr_servers"))
    assert len(saved) == 1


# ─── Resilience against malformed stored JSON ──────────────────────────────

async def test_test_arr_instance_masked_key_resolution_survives_corrupt_stored_json(fresh_db):
    """A corrupted radarr_servers setting must not crash the connectivity
    test — it degrades to 'key not resolved' instead of raising."""
    from backend.services.arr_service import test_arr_instance

    await set_setting("radarr_servers", "{not valid json")
    result = await test_arr_instance("radarr", "http://radarr.local:7878", "***")
    assert result["ok"] is False  # must not raise


# NOTE: a test for "corrupted existing radarr_servers/sonarr_servers setting"
# is intentionally NOT included here — see BUGS SUSPECTÉS in the final
# report. _merge()'s internal `except Exception: existing = []` (lines
# 162-163) DOES protect the merge itself, but sync_arr_from_seerr() then
# re-parses the same raw setting string UNPROTECTED at lines 176-177
# (`prev_r = len(json.loads(raw_radarr or "[]") or [])`) just to compute the
# "N added" count — which raises an unhandled JSONDecodeError, even though
# the merge+save immediately before it (lines 170-174) already succeeded.
# Lines 162-163 are therefore unreachable through the public API without
# also hitting that crash; a test asserting the crash as correct behavior
# would certify the bug.
