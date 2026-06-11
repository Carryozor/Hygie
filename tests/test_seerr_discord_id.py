"""Seerr Discord ID extraction — API moved from discordId (str) to discordIds (list).

Bug: recent Seerr/Jellyseerr versions return `discordIds: ["…"]` from
/api/v1/user/{id}/settings/notifications; the legacy `discordId` field is gone.
Hygie still read `discordId`, so every user came back without a Discord ID.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── Unit: _extract_discord_id ────────────────────────────────────────────────

def test_extract_discord_id_new_list_format():
    from backend.arr_clients.seerr import _extract_discord_id
    assert _extract_discord_id({"discordIds": ["164815676127707136"]}) == "164815676127707136"


def test_extract_discord_id_legacy_string_format():
    from backend.arr_clients.seerr import _extract_discord_id
    assert _extract_discord_id({"discordId": "123456789"}) == "123456789"


def test_extract_discord_id_prefers_new_format():
    from backend.arr_clients.seerr import _extract_discord_id
    assert _extract_discord_id({"discordIds": ["111"], "discordId": "222"}) == "111"


def test_extract_discord_id_empty_cases():
    from backend.arr_clients.seerr import _extract_discord_id
    assert _extract_discord_id({}) == ""
    assert _extract_discord_id({"discordIds": []}) == ""
    assert _extract_discord_id({"discordIds": [None]}) == ""
    assert _extract_discord_id({"discordIds": ["  "]}) == ""
    assert _extract_discord_id({"discordId": None}) == ""


# ─── Wiring: seerr_get_users picks up discordIds ──────────────────────────────

def _mock_async_client(get_responses):
    """Return a MagicMock standing in for httpx.AsyncClient — `get` cycles
    through get_responses keyed by URL substring."""
    client = MagicMock()

    async def _get(url, *a, **kw):
        for fragment, payload in get_responses.items():
            if fragment in url:
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = payload
                return resp
        resp = MagicMock()
        resp.status_code = 404
        resp.json.return_value = {}
        return resp

    client.get = AsyncMock(side_effect=_get)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def test_seerr_get_users_reads_discord_ids_list(monkeypatch):
    import backend.arr_clients.seerr as seerr_mod

    monkeypatch.setattr(
        seerr_mod, "_seerr_config",
        AsyncMock(return_value=("http://seerr:5055", "key")),
    )

    # DB lookup for manual mappings must not interfere
    db_cm = MagicMock()
    db = MagicMock()
    db.fetch_all = AsyncMock(return_value=[])
    db_cm.__aenter__ = AsyncMock(return_value=db)
    db_cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(seerr_mod, "get_db", lambda: db_cm)

    responses = {
        "/settings/notifications": {"discordIds": ["164815676127707136"], "discordEnabled": True},
        "/api/v1/user": {
            "pageInfo": {"results": 1},
            "results": [{"id": 4, "displayName": "Blork"}],
        },
    }
    with patch.object(seerr_mod.httpx, "AsyncClient", return_value=_mock_async_client(responses)):
        users = await seerr_mod.seerr_get_users()

    assert len(users) == 1
    assert users[0]["username"] == "Blork"
    assert users[0]["discord_id"] == "164815676127707136"
    assert users[0]["discord_id_seerr"] == "164815676127707136"


# ─── Wiring: discord_client._resolve_discord_id fallback ─────────────────────

async def test_resolve_discord_id_seerr_fallback_reads_list(monkeypatch):
    import backend.discord_client as dc

    async def _fake_get_setting(key):
        return {"seerr_url": "http://seerr:5055", "seerr_api_key": "key"}.get(key, "")

    monkeypatch.setattr(dc, "get_setting", _fake_get_setting)

    # DB lookup raises → falls through to the Seerr HTTP fallback
    failing_cm = MagicMock()
    failing_cm.__aenter__ = AsyncMock(side_effect=RuntimeError("no db"))
    failing_cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(dc, "get_db", lambda: failing_cm)

    responses = {"/settings/notifications": {"discordIds": ["999888777"]}}
    with patch.object(dc.httpx, "AsyncClient", return_value=_mock_async_client(responses)):
        result = await dc._resolve_discord_id(4)

    assert result == "999888777"
