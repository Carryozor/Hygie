"""P2 — qBittorrent actions: honour delete_torrent vs delete_files, implement
pause, verify the real effect, trace failures (add_log + step_warnings)."""
from unittest.mock import AsyncMock, patch

import pytest

from tests._qbit_plan_stub import echo_qbit_plan  # noqa: F401  (fixture)

import backend.qbit_client as qbit


class _R:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else []

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _sleep(_d):
        return None
    monkeypatch.setattr(qbit.asyncio, "sleep", _sleep)


def _fake_request(calls, info_sequence, delete_status=200, pause_statuses=None):
    """info_sequence: successive payloads returned by /torrents/info."""
    info = list(info_sequence)
    pauses = list(pause_statuses or [])

    async def _req(method, path, **kw):
        calls.append((method, path, kw))
        if path == "/api/v2/torrents/info":
            return _R(200, info.pop(0) if len(info) > 1 else info[0])
        if path == "/api/v2/torrents/delete":
            return _R(delete_status)
        if path in ("/api/v2/torrents/stop", "/api/v2/torrents/pause"):
            return _R(pauses.pop(0) if pauses else 200)
        return _R(200)
    return _req


# ── qbit_delete_torrent verifies the effect ──────────────────────────────────

async def test_delete_torrent_false_when_hash_still_present_after_200(monkeypatch):
    calls = []
    still_there = [{"hash": "deadbeef"}]
    monkeypatch.setattr(qbit, "_request", _fake_request(calls, [still_there]))
    assert await qbit.qbit_delete_torrent("DEADBEEF", delete_files=True) is False


async def test_delete_torrent_true_when_hash_gone(monkeypatch):
    calls = []
    monkeypatch.setattr(qbit, "_request", _fake_request(calls, [[]]))
    assert await qbit.qbit_delete_torrent("DEADBEEF", delete_files=False) is True
    info_calls = [c for c in calls if c[1] == "/api/v2/torrents/info"]
    assert info_calls and info_calls[-1][2]["params"] == {"hashes": "DEADBEEF"}


async def test_delete_torrent_false_when_verification_unavailable(monkeypatch):
    async def _req(method, path, **kw):
        if path == "/api/v2/torrents/delete":
            return _R(200)
        return None  # info lookup fails
    monkeypatch.setattr(qbit, "_request", _req)
    assert await qbit.qbit_delete_torrent("DEADBEEF") is False


# ── qbit_pause_torrent ───────────────────────────────────────────────────────

async def test_pause_uses_stop_on_v5(monkeypatch):
    calls = []
    monkeypatch.setattr(qbit, "_request", _fake_request(calls, [[]]))
    assert await qbit.qbit_pause_torrent("abc") is True
    paths = [c[1] for c in calls]
    assert "/api/v2/torrents/stop" in paths and "/api/v2/torrents/pause" not in paths


async def test_pause_falls_back_to_pause_on_v4_404(monkeypatch):
    calls = []
    monkeypatch.setattr(qbit, "_request", _fake_request(calls, [[]], pause_statuses=[404, 200]))
    assert await qbit.qbit_pause_torrent("abc") is True
    assert [c[1] for c in calls] == ["/api/v2/torrents/stop", "/api/v2/torrents/pause"]


async def test_pause_false_when_request_fails(monkeypatch):
    async def _req(method, path, **kw):
        return None
    monkeypatch.setattr(qbit, "_request", _req)
    assert await qbit.qbit_pause_torrent("abc") is False


# ── _handle_qbit dispatch ────────────────────────────────────────────────────

async def test_delete_torrent_action_keeps_files():
    from backend.deletion_helpers import _handle_qbit
    with patch("backend.deletion_helpers.qbit_delete_torrent", new=AsyncMock(return_value=True)) as m:
        await _handle_qbit("h", "T", "delete_torrent", "tag")
    m.assert_awaited_once_with("h", delete_files=False)


async def test_delete_files_action_removes_files():
    from backend.deletion_helpers import _handle_qbit
    with patch("backend.deletion_helpers.qbit_delete_torrent", new=AsyncMock(return_value=True)) as m:
        await _handle_qbit("h", "T", "delete_files", "tag")
    m.assert_awaited_once_with("h", delete_files=True)


async def test_pause_action_pauses_and_tags():
    from backend.deletion_helpers import _handle_qbit
    with (
        patch("backend.deletion_helpers.qbit_pause_torrent", new=AsyncMock(return_value=True)) as p,
        patch("backend.deletion_helpers.qbit_add_tag", new=AsyncMock(return_value=True)) as t,
        patch("backend.deletion_helpers.qbit_delete_torrent", new=AsyncMock()) as d,
    ):
        ok = await _handle_qbit("h", "T", "pause", "tag")
    assert ok is True
    p.assert_awaited_once_with("h")
    t.assert_awaited_once_with("h", "tag")
    d.assert_not_awaited()


@pytest.mark.parametrize("action,target", [
    ("delete_torrent", "qbit_delete_torrent"),
    ("delete_files", "qbit_delete_torrent"),
    ("pause", "qbit_pause_torrent"),
    ("tag_only", "qbit_add_tag"),
])
async def test_failure_is_logged_warn_and_recorded_in_warnings(action, target):
    from backend.deletion_helpers import _handle_qbit
    warnings: list = []
    with (
        patch(f"backend.deletion_helpers.{target}", new=AsyncMock(return_value=False)),
        patch("backend.deletion_helpers.add_log", new=AsyncMock()) as log,
    ):
        ok = await _handle_qbit("h", "Inception", action, "tag", warnings)
    assert ok is False
    assert log.await_args.args[0] == "WARN"
    assert len(warnings) == 1 and "h" in warnings[0]


async def test_exception_is_recorded_in_warnings():
    from backend.deletion_helpers import _handle_qbit
    warnings: list = []
    with (
        patch("backend.deletion_helpers.qbit_delete_torrent", new=AsyncMock(side_effect=RuntimeError("boom"))),
        patch("backend.deletion_helpers.add_log", new=AsyncMock()),
    ):
        ok = await _handle_qbit("h", "T", "delete_files", "tag", warnings)
    assert ok is False and any("boom" in w for w in warnings)


# ── QbitStep: missing hash is traced ─────────────────────────────────────────

async def test_qbit_step_missing_hash_warns():
    from backend.deletion_pipeline import DeletionContext, QbitStep
    ctx = DeletionContext(item={"title": "X", "id": 1}, dry_run=False, qbit_action="delete_files")
    with patch("backend.db.logs.add_log", new=AsyncMock()) as log:
        await QbitStep().execute(ctx)
    assert log.await_args.args[0] == "WARN"
    assert len(ctx.step_warnings) == 1


@pytest.mark.usefixtures("echo_qbit_plan")
async def test_qbit_step_passes_ctx_warnings_to_handler():
    from backend.deletion_pipeline import DeletionContext, QbitStep
    ctx = DeletionContext(item={"title": "X"}, dry_run=False, qbit_action="delete_files")
    ctx.torrent_hash = "h1"
    with patch("backend.deletion_helpers._handle_qbit", new=AsyncMock(return_value=True)) as m:
        await QbitStep().execute(ctx)
    assert m.await_args.args[-1] is ctx.step_warnings


# ── Movie fallback by path ───────────────────────────────────────────────────

async def test_movie_with_radarr_id_but_no_history_falls_back_to_path():
    from backend.deletion_helpers import _find_torrent_hash
    row = {"media_type": "Movie", "radarr_id": 7, "file_path": "/data/movies/M/M.mkv"}
    with (
        patch("backend.deletion_helpers.radarr_get_torrent_hash_any", new=AsyncMock(return_value=None)),
        patch("backend.deletion_helpers.qbit_find_by_path", new=AsyncMock(return_value="pathhash")) as f,
    ):
        assert await _find_torrent_hash(row) == "pathhash"
    f.assert_awaited_once_with("/data/movies/M/M.mkv")


async def test_movie_history_hash_wins_over_path():
    from backend.deletion_helpers import _find_torrent_hash
    row = {"media_type": "Movie", "radarr_id": 7, "file_path": "/data/movies/M/M.mkv"}
    with (
        patch("backend.deletion_helpers.radarr_get_torrent_hash_any", new=AsyncMock(return_value="histhash")),
        patch("backend.deletion_helpers.qbit_find_by_path", new=AsyncMock()) as f,
    ):
        assert await _find_torrent_hash(row) == "histhash"
    f.assert_not_awaited()
