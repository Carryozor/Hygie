"""A consolidated row in retry whose target is already gone must stay deletable.

An 'error' row with attempts > 0 has often already had its Sonarr series /
Season item removed by an earlier step of the pipeline. "Target not found"
then no longer means "unknown watch state": nothing is left to protect, the
row goes through (4.5 behaviour). A media server that is DOWN still postpones.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

URL = "http://emby.retry:8096"
LIB = {"lib1": "0"}


def _iso(delta_h: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=delta_h)).isoformat()


@pytest.fixture(autouse=True)
async def _fresh_db(monkeypatch, tmp_path):
    import backend.db.engine as _db_engine
    import backend.db.schema as _db_schema
    import backend.db.settings_store as _db_ss
    import backend.db.utils as _db_utils
    import backend.deletion as _deletion

    db_path = str(tmp_path / "retry.db")
    for mod, attr in ((_db_utils, "DB_PATH"), (_db_schema, "DB_PATH"),
                      (_deletion, "DB_PATH"), (_db_engine, "SQLITE_PATH")):
        monkeypatch.setattr(mod, attr, db_path)
    monkeypatch.setattr(_db_engine, "DIALECT", "sqlite")
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()


@pytest.fixture
def env(monkeypatch):
    async def _client(server_id="0"):
        return URL, "k"
    monkeypatch.setattr("backend.emby_client.get_client", _client)
    monkeypatch.setattr("backend.deletion.get_client", _client)
    with (
        patch("backend.deletion.get_play_activity", new=AsyncMock(return_value={})),
        patch("backend.media_item_resolver.sonarr_get_series_by_id_any",
              new=AsyncMock(return_value={"path": "/media/grimm"})) as sonarr,
        patch("backend.deletion.update_activity_log_batch", new=AsyncMock()),
        patch("backend.deletion.update_consolidated_watch_state", new=AsyncMock()),
        patch("backend.deletion.add_log", new=AsyncMock()),
    ):
        yield type("Env", (), {"sonarr": sonarr})


async def _queue(emby_id, *, season=None, attempts=1) -> None:
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name,"
            " file_path, detected_at, delete_at, status, sonarr_series_id, season_number,"
            " attempts, next_retry_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (emby_id, "Grimm", "series", "lib1", "Series", "/media/grimm/s01/e01.mkv",
             _iso(-48), _iso(-24), "error" if attempts else "pending", 304, season,
             attempts, _iso(-1) if attempts else None),
        )
        await db.commit()


async def _selected() -> list:
    from backend.deletion import _select_items_to_delete
    return [r["emby_id"] for r in await _select_items_to_delete(LIB)]


async def test_retry_series_unknown_to_sonarr_stays_selected(env):
    env.sonarr.return_value = None  # ArrStep already removed the series
    await _queue("sonarr-series:304")
    assert await _selected() == ["sonarr-series:304"]


@respx.mock
async def test_retry_season_item_gone_from_emby_stays_selected(env):
    respx.get(f"{URL}/Items").mock(return_value=httpx.Response(200, json={"Items": []}))
    await _queue("sonarr-season:304:1", season=1)
    assert await _selected() == ["sonarr-season:304:1"]


@respx.mock
async def test_retry_with_emby_down_is_still_postponed(env):
    respx.get(f"{URL}/Items").mock(return_value=httpx.Response(500))
    await _queue("sonarr-season:304:1", season=1)
    assert await _selected() == []


async def test_first_attempt_target_unknown_is_still_postponed(env):
    env.sonarr.return_value = None
    await _queue("sonarr-series:304", attempts=0)
    assert await _selected() == []
