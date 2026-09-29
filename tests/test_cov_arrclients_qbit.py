"""Coverage tests for backend/qbit_client.py — qBittorrent client.

Priority per the mission brief: login/cookie handling, and torrent deletion
with/without files (exact hashes + deleteFiles value sent).
"""
import os

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")

import httpx
import pytest
from pytest_httpx import HTTPXMock

import backend.qbit_client as qbit


@pytest.fixture(autouse=True)
def _reset_module_globals():
    """qbit_client keeps SID cookie + alert cooldown as module globals — leak
    between tests would make later tests depend on earlier ones' outcomes."""
    qbit._sid_cookie = None
    qbit._proxy_alert_ts = 0.0
    yield
    qbit._sid_cookie = None
    qbit._proxy_alert_ts = 0.0


def _settings(**overrides):
    base = {
        "qbit_proxy_url": "", "qbit_url": "http://qbit.test:8080",
        "qbit_user": "admin", "qbit_password": "secret",
    }
    base.update(overrides)

    async def _get_setting(key):
        return base.get(key, "")
    return _get_setting


# ─── _extract_sid ───────────────────────────────────────────────────────────────

def test_extract_sid_returns_sid_cookie():
    assert qbit._extract_sid({"SID": "abc123"}) == "abc123"


def test_extract_sid_falls_back_to_qbt_sid_prefixed_cookie():
    assert qbit._extract_sid({"QBT_SID_8080": "xyz"}) == "xyz"


def test_extract_sid_returns_none_when_absent():
    assert qbit._extract_sid({"other": "value"}) is None


# ─── _login ─────────────────────────────────────────────────────────────────────

async def test_login_success_204_stores_sid(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="http://qbit.test:8080/api/v2/auth/login", status_code=204,
        headers=[("set-cookie", "SID=newsid123; path=/")],
    )
    async with httpx.AsyncClient() as c:
        ok = await qbit._login(c, "http://qbit.test:8080", "admin", "secret")
    assert ok is True
    assert qbit._sid_cookie == "newsid123"


async def test_login_success_200_ok_text_stores_sid(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="http://qbit.test:8080/api/v2/auth/login", status_code=200, text="Ok.",
        headers=[("set-cookie", "SID=legacy; path=/")],
    )
    async with httpx.AsyncClient() as c:
        ok = await qbit._login(c, "http://qbit.test:8080", "admin", "secret")
    assert ok is True
    assert qbit._sid_cookie == "legacy"


async def test_login_204_without_cookie_still_authenticated(httpx_mock: HTTPXMock):
    """Bypass-auth mode: 204 with no Set-Cookie is still a successful login."""
    httpx_mock.add_response(url="http://qbit.test:8080/api/v2/auth/login", status_code=204)
    async with httpx.AsyncClient() as c:
        ok = await qbit._login(c, "http://qbit.test:8080", "admin", "secret")
    assert ok is True


async def test_login_fails_on_wrong_credentials(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="http://qbit.test:8080/api/v2/auth/login", status_code=200, text="Fails."
    )
    async with httpx.AsyncClient() as c:
        ok = await qbit._login(c, "http://qbit.test:8080", "admin", "wrong")
    assert ok is False


async def test_login_returns_false_on_exception(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(httpx.ConnectError("down"))
    async with httpx.AsyncClient() as c:
        ok = await qbit._login(c, "http://qbit.test:8080", "admin", "secret")
    assert ok is False


# ─── _try_url ───────────────────────────────────────────────────────────────────

async def test_try_url_returns_response_when_authenticated(httpx_mock: HTTPXMock):
    qbit._sid_cookie = "existing-sid"
    httpx_mock.add_response(
        url="http://qbit.test:8080/api/v2/torrents/info", status_code=200, json=[]
    )
    async with httpx.AsyncClient() as c:
        r = await qbit._try_url(c, "http://qbit.test:8080", "GET", "/api/v2/torrents/info", "admin", "secret")
    assert r.status_code == 200
    req = httpx_mock.get_requests()[0]
    assert req.headers.get("cookie") == "SID=existing-sid"


async def test_try_url_relogins_on_403_and_retries(httpx_mock: HTTPXMock):
    qbit._sid_cookie = "expired-sid"
    httpx_mock.add_response(
        url="http://qbit.test:8080/api/v2/torrents/info", status_code=403
    )
    httpx_mock.add_response(
        url="http://qbit.test:8080/api/v2/auth/login", status_code=204,
        headers=[("set-cookie", "SID=fresh-sid; path=/")],
    )
    httpx_mock.add_response(
        url="http://qbit.test:8080/api/v2/torrents/info", status_code=200, json=[{"hash": "x"}]
    )
    async with httpx.AsyncClient() as c:
        r = await qbit._try_url(c, "http://qbit.test:8080", "GET", "/api/v2/torrents/info", "admin", "secret")
    assert r.status_code == 200
    assert qbit._sid_cookie == "fresh-sid"


async def test_try_url_403_without_credentials_stays_403(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="http://qbit.test:8080/api/v2/torrents/info", status_code=403)
    async with httpx.AsyncClient() as c:
        r = await qbit._try_url(c, "http://qbit.test:8080", "GET", "/api/v2/torrents/info", "", "")
    assert r.status_code == 403


async def test_try_url_returns_none_on_exception(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(httpx.ConnectError("down"))
    async with httpx.AsyncClient() as c:
        r = await qbit._try_url(c, "http://qbit.test:8080", "GET", "/api/v2/torrents/info", "admin", "secret")
    assert r is None


# ─── _alert_proxy_fallback ──────────────────────────────────────────────────────

async def test_alert_proxy_fallback_sends_alert(monkeypatch):
    sent = []

    async def _send_alert(title, desc, level="error", **kw):
        sent.append((title, level))
        return True

    import backend.discord_client as discord_mod
    monkeypatch.setattr(discord_mod, "send_alert", _send_alert)

    await qbit._alert_proxy_fallback()
    assert len(sent) == 1
    assert sent[0][1] == "warning"


async def test_alert_proxy_fallback_respects_cooldown(monkeypatch):
    sent = []

    async def _send_alert(title, desc, level="error", **kw):
        sent.append(title)
        return True

    import backend.discord_client as discord_mod
    monkeypatch.setattr(discord_mod, "send_alert", _send_alert)

    await qbit._alert_proxy_fallback()
    await qbit._alert_proxy_fallback()
    assert len(sent) == 1


async def test_alert_proxy_fallback_swallows_send_exception(monkeypatch):
    async def _send_alert(*a, **kw):
        raise RuntimeError("discord down")

    import backend.discord_client as discord_mod
    monkeypatch.setattr(discord_mod, "send_alert", _send_alert)

    await qbit._alert_proxy_fallback()  # must not raise


# ─── _request ───────────────────────────────────────────────────────────────────

async def test_request_returns_none_when_nothing_configured(monkeypatch):
    monkeypatch.setattr(qbit, "get_setting", _settings(qbit_proxy_url="", qbit_url=""))
    assert await qbit._request("GET", "/api/v2/torrents/info") is None


async def test_request_uses_direct_url_when_no_proxy(monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr(qbit, "get_setting", _settings(qbit_proxy_url=""))
    qbit._sid_cookie = "sid"
    httpx_mock.add_response(url="http://qbit.test:8080/api/v2/torrents/info", status_code=200, json=[])
    r = await qbit._request("GET", "/api/v2/torrents/info")
    assert r.status_code == 200


async def test_request_prefers_proxy_over_direct(monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr(
        qbit, "get_setting",
        _settings(qbit_proxy_url="http://proxy.test:8080", qbit_url="http://direct.test:8080"),
    )
    qbit._sid_cookie = "sid"
    httpx_mock.add_response(url="http://proxy.test:8080/api/v2/torrents/info", status_code=200, json=[])
    r = await qbit._request("GET", "/api/v2/torrents/info")
    assert r.status_code == 200
    assert httpx_mock.get_requests()[0].url.host == "proxy.test"


async def test_request_falls_back_to_direct_when_proxy_unreachable(monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr(
        qbit, "get_setting",
        _settings(qbit_proxy_url="http://proxy.test:8080", qbit_url="http://direct.test:8080"),
    )
    qbit._sid_cookie = "sid"
    httpx_mock.add_exception(httpx.ConnectError("proxy down"), url="http://proxy.test:8080/api/v2/torrents/info")
    httpx_mock.add_response(url="http://direct.test:8080/api/v2/torrents/info", status_code=200, json=[])

    sent = []
    async def _send_alert(title, desc, level="error", **kw):
        sent.append(title)
        return True
    import backend.discord_client as discord_mod
    monkeypatch.setattr(discord_mod, "send_alert", _send_alert)

    r = await qbit._request("GET", "/api/v2/torrents/info")
    assert r.status_code == 200
    assert httpx_mock.get_requests()[-1].url.host == "direct.test"
    assert len(sent) == 1  # proxy-fallback alert fired


async def test_request_proxy_only_no_direct_fallback_returns_none(monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr(qbit, "get_setting", _settings(qbit_proxy_url="http://proxy.test:8080", qbit_url=""))
    httpx_mock.add_exception(httpx.ConnectError("proxy down"))
    assert await qbit._request("GET", "/api/v2/torrents/info") is None


async def test_request_proxy_equals_direct_no_double_attempt(monkeypatch, httpx_mock: HTTPXMock):
    """proxy_url == direct_url: on proxy failure, must not retry the same URL again."""
    monkeypatch.setattr(
        qbit, "get_setting",
        _settings(qbit_proxy_url="http://same.test:8080", qbit_url="http://same.test:8080"),
    )
    httpx_mock.add_exception(httpx.ConnectError("down"))
    assert await qbit._request("GET", "/api/v2/torrents/info") is None
    assert len(httpx_mock.get_requests()) == 1


# ─── _test_url_fresh ────────────────────────────────────────────────────────────

async def test_test_url_fresh_success(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="http://qbit.test:8080/api/v2/auth/login", status_code=204,
        headers=[("set-cookie", "SID=freshsid; path=/")],
    )
    httpx_mock.add_response(url="http://qbit.test:8080/api/v2/app/version", status_code=200, text="v4.6.0")
    async with httpx.AsyncClient() as c:
        ok, detail = await qbit._test_url_fresh(c, "http://qbit.test:8080", "admin", "secret")
    assert ok is True
    assert detail == "v4.6.0"


async def test_test_url_fresh_auth_failure(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="http://qbit.test:8080/api/v2/auth/login", status_code=200, text="Fails."
    )
    async with httpx.AsyncClient() as c:
        ok, detail = await qbit._test_url_fresh(c, "http://qbit.test:8080", "admin", "wrong")
    assert ok is False
    assert detail == "authentification échouée"


async def test_test_url_fresh_version_http_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="http://qbit.test:8080/api/v2/auth/login", status_code=204)
    httpx_mock.add_response(url="http://qbit.test:8080/api/v2/app/version", status_code=500)
    async with httpx.AsyncClient() as c:
        ok, detail = await qbit._test_url_fresh(c, "http://qbit.test:8080", "admin", "secret")
    assert ok is False
    assert detail == "HTTP 500"


async def test_test_url_fresh_returns_false_on_exception(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(httpx.ConnectError("down"))
    async with httpx.AsyncClient() as c:
        ok, detail = await qbit._test_url_fresh(c, "http://qbit.test:8080", "admin", "secret")
    assert ok is False


# ─── test_qui ───────────────────────────────────────────────────────────────────

async def test_qui_not_configured(monkeypatch):
    monkeypatch.setattr(qbit, "get_setting", _settings(qbit_proxy_url=""))
    ok, msg = await qbit.test_qui()
    assert ok is False
    assert msg == "Proxy QUI non configuré"


async def test_qui_success(monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr(qbit, "get_setting", _settings(qbit_proxy_url="http://proxy.test:8080"))
    httpx_mock.add_response(
        url="http://proxy.test:8080/api/v2/auth/login", status_code=204,
        headers=[("set-cookie", "SID=s; path=/")],
    )
    httpx_mock.add_response(url="http://proxy.test:8080/api/v2/app/version", status_code=200, text="v5.0.0")
    ok, msg = await qbit.test_qui()
    assert ok is True
    assert "v5.0.0" in msg


async def test_qui_failure_reports_detail(monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr(qbit, "get_setting", _settings(qbit_proxy_url="http://proxy.test:8080"))
    httpx_mock.add_response(
        url="http://proxy.test:8080/api/v2/auth/login", status_code=200, text="Fails."
    )
    ok, msg = await qbit.test_qui()
    assert ok is False
    assert "❌" in msg


# ─── test_qbit ──────────────────────────────────────────────────────────────────

async def test_qbit_not_configured(monkeypatch):
    monkeypatch.setattr(qbit, "get_setting", _settings(qbit_proxy_url="", qbit_url=""))
    ok, msg = await qbit.test_qbit()
    assert ok is False
    assert msg == "Non configuré"


async def test_qbit_reports_both_proxy_and_direct(monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr(
        qbit, "get_setting",
        _settings(qbit_proxy_url="http://proxy.test:8080", qbit_url="http://direct.test:8080"),
    )
    httpx_mock.add_response(
        url="http://proxy.test:8080/api/v2/auth/login", status_code=204,
        headers=[("set-cookie", "SID=p; path=/")],
    )
    httpx_mock.add_response(url="http://proxy.test:8080/api/v2/app/version", status_code=200, text="v1")
    httpx_mock.add_response(
        url="http://direct.test:8080/api/v2/auth/login", status_code=200, text="Fails."
    )
    ok, msg = await qbit.test_qbit()
    assert ok is True  # any_ok — proxy succeeded
    assert "Proxy QUI ✅" in msg
    assert "Direct ❌" in msg


async def test_qbit_reports_proxy_failure_and_direct_success(monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr(
        qbit, "get_setting",
        _settings(qbit_proxy_url="http://proxy.test:8080", qbit_url="http://direct.test:8080"),
    )
    httpx_mock.add_response(
        url="http://proxy.test:8080/api/v2/auth/login", status_code=200, text="Fails."
    )
    httpx_mock.add_response(
        url="http://direct.test:8080/api/v2/auth/login", status_code=204,
        headers=[("set-cookie", "SID=d; path=/")],
    )
    httpx_mock.add_response(url="http://direct.test:8080/api/v2/app/version", status_code=200, text="v2")

    ok, msg = await qbit.test_qbit()
    assert ok is True  # direct succeeded
    assert "Proxy QUI ❌" in msg
    assert "Direct ✅" in msg


async def test_qbit_direct_only_when_equal_to_proxy_not_duplicated(monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr(
        qbit, "get_setting",
        _settings(qbit_proxy_url="http://same.test:8080", qbit_url="http://same.test:8080"),
    )
    httpx_mock.add_response(
        url="http://same.test:8080/api/v2/auth/login", status_code=204,
        headers=[("set-cookie", "SID=s; path=/")],
    )
    httpx_mock.add_response(url="http://same.test:8080/api/v2/app/version", status_code=200, text="v1")
    ok, msg = await qbit.test_qbit()
    assert ok is True
    assert msg.count("✅") == 1  # only tested once, not twice


# ─── qbit_find_by_path — additional edge cases beyond test_arr_clients.py ──────

async def test_find_by_path_returns_none_for_empty_path():
    assert await qbit.qbit_find_by_path("") is None


async def test_find_by_path_returns_none_when_request_fails(monkeypatch):
    async def _req(*a, **k):
        return None
    monkeypatch.setattr(qbit, "_request", _req)
    assert await qbit.qbit_find_by_path("/x.mkv") is None


async def test_find_by_path_returns_none_on_non_200(monkeypatch):
    class _R:
        status_code = 500
    async def _req(*a, **k):
        return _R()
    monkeypatch.setattr(qbit, "_request", _req)
    assert await qbit.qbit_find_by_path("/x.mkv") is None


async def test_find_by_path_falls_back_to_save_path_and_name(monkeypatch):
    class _R:
        status_code = 200
        def json(self):
            return [{"hash": "AAA", "name": "Movie", "save_path": "/dl/movies", "content_path": ""}]
    async def _req(*a, **k):
        return _R()
    monkeypatch.setattr(qbit, "_request", _req)
    assert await qbit.qbit_find_by_path("/dl/movies/Movie/x.mkv") == "aaa"


async def test_find_by_path_skips_torrent_with_no_resolvable_path(monkeypatch):
    """A torrent with neither content_path nor (save_path+name) contributes
    no candidate path and must be skipped, not matched by accident."""
    class _R:
        status_code = 200
        def json(self):
            return [
                {"hash": "NOPE", "name": "", "save_path": "", "content_path": ""},
                {"hash": "GOOD", "name": "Movie", "save_path": "/dl", "content_path": "/dl/Movie.mkv"},
            ]
    async def _req(*a, **k):
        return _R()
    monkeypatch.setattr(qbit, "_request", _req)
    assert await qbit.qbit_find_by_path("/dl/Movie.mkv") == "good"


async def test_find_by_path_swallows_parse_exception(monkeypatch):
    class _R:
        status_code = 200
        def json(self):
            raise ValueError("bad json")
    async def _req(*a, **k):
        return _R()
    monkeypatch.setattr(qbit, "_request", _req)
    assert await qbit.qbit_find_by_path("/x.mkv") is None


# ─── qbit_add_tag ───────────────────────────────────────────────────────────────

async def test_add_tag_returns_false_without_hash_or_tag():
    assert await qbit.qbit_add_tag("", "tag") is False
    assert await qbit.qbit_add_tag("hash", "") is False


async def test_add_tag_creates_tag_then_adds_it(monkeypatch):
    calls = []
    class _R:
        status_code = 200
    async def _req(method, path, **kw):
        calls.append((method, path, kw))
        return _R()
    monkeypatch.setattr(qbit, "_request", _req)

    ok = await qbit.qbit_add_tag("abc123", "hygie-deleted")
    assert ok is True
    assert calls[0] == ("POST", "/api/v2/torrents/createTags", {"data": {"tags": "hygie-deleted"}})
    assert calls[1] == (
        "POST", "/api/v2/torrents/addTags",
        {"data": {"hashes": "abc123", "tags": "hygie-deleted"}},
    )


async def test_add_tag_returns_false_when_request_fails(monkeypatch):
    async def _req(method, path, **kw):
        return None
    monkeypatch.setattr(qbit, "_request", _req)
    assert await qbit.qbit_add_tag("abc123", "tag") is False


# ─── qbit_delete_torrent — CRITICAL: exact hash + deleteFiles ──────────────────

async def test_delete_torrent_returns_false_without_hash():
    assert await qbit.qbit_delete_torrent("") is False


async def test_delete_torrent_sends_exact_hash_and_delete_files_true(monkeypatch):
    captured = {}
    class _R:
        status_code = 200
    async def _req(method, path, **kw):
        captured.update(method=method, path=path, **kw)
        return _R()
    monkeypatch.setattr(qbit, "_request", _req)

    ok = await qbit.qbit_delete_torrent("DEADBEEF", delete_files=True)
    assert ok is True
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v2/torrents/delete"
    assert captured["data"] == {"hashes": "DEADBEEF", "deleteFiles": "true"}


async def test_delete_torrent_sends_delete_files_false_when_keeping_files(monkeypatch):
    captured = {}
    class _R:
        status_code = 204
    async def _req(method, path, **kw):
        captured.update(**kw)
        return _R()
    monkeypatch.setattr(qbit, "_request", _req)

    ok = await qbit.qbit_delete_torrent("DEADBEEF", delete_files=False)
    assert ok is True
    assert captured["data"] == {"hashes": "DEADBEEF", "deleteFiles": "false"}


async def test_delete_torrent_returns_false_when_request_fails(monkeypatch):
    async def _req(method, path, **kw):
        return None
    monkeypatch.setattr(qbit, "_request", _req)
    assert await qbit.qbit_delete_torrent("DEADBEEF") is False


async def test_delete_torrent_returns_false_on_non_2xx(monkeypatch):
    class _R:
        status_code = 404
    async def _req(method, path, **kw):
        return _R()
    monkeypatch.setattr(qbit, "_request", _req)
    assert await qbit.qbit_delete_torrent("DEADBEEF") is False
