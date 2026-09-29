"""Full coverage for backend/db/websocket.py's tiny client registry.

_broadcast is a deliberate no-op (log delivery moved to DB polling) — the
test only proves it stays a safe no-op (doesn't raise, returns None), not
that it delivers anything.
"""
import pytest

from backend.db.websocket import register_ws, unregister_ws, _broadcast, _ws_clients


@pytest.fixture(autouse=True)
def _clean_registry():
    _ws_clients.clear()
    yield
    _ws_clients.clear()


def test_register_ws_adds_client():
    ws = object()
    register_ws(ws)
    assert ws in _ws_clients


def test_unregister_ws_removes_client():
    ws = object()
    register_ws(ws)
    unregister_ws(ws)
    assert ws not in _ws_clients


def test_unregister_ws_unknown_client_is_a_noop():
    unregister_ws(object())  # never registered — discard() must not raise


async def test_broadcast_is_a_noop_and_returns_none():
    assert await _broadcast({"type": "log", "message": "hi"}) is None
