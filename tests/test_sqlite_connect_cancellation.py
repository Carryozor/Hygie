"""Cancelling a task while get_db() is still opening its SQLite connection
must not leak the aiosqlite worker thread.

aiosqlite 0.20's Connection._connect() only cleans up on `except Exception`,
so an asyncio.CancelledError delivered while it awaits the connection future
leaves the (non-daemon) worker thread running forever. That thread then blocks
interpreter exit — the intermittent "pytest reports all tests passed, then
hangs" seen since 2026-09-18 — and in a long-running SQLite deployment leaks
one thread per unlucky cancellation (e.g. the storage prewarm task cancelled
at shutdown).
"""
import asyncio
import threading

import aiosqlite
import pytest

import backend.db.engine as engine


def _aiosqlite_threads() -> set:
    return {t for t in threading.enumerate() if isinstance(t, aiosqlite.Connection)}


@pytest.fixture
def sqlite_path(tmp_path, monkeypatch):
    path = str(tmp_path / "cancel.db")
    monkeypatch.setattr(engine, "DIALECT", "sqlite")
    monkeypatch.setattr(engine, "SQLITE_PATH", path)
    return path


async def _open_and_hold():
    async with engine.get_db() as db:
        await db.fetch_one("SELECT 1 AS x")


async def test_cancel_during_connect_does_not_leak_worker_thread(sqlite_path):
    before = _aiosqlite_threads()
    task = asyncio.create_task(_open_and_hold())
    # One loop iteration: get_db() has started the worker thread and is now
    # suspended on the connection future — the window aiosqlite mishandles.
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    leaked = _aiosqlite_threads() - before
    for t in leaked:
        t.join(timeout=2)
    assert not [t for t in leaked if t.is_alive()], "aiosqlite worker thread leaked after cancellation"


async def test_worker_threads_never_block_interpreter_exit(sqlite_path):
    seen: list = []
    real_connect = aiosqlite.connect

    def spy(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        seen.append(conn)
        return conn

    engine_aiosqlite = pytest.MonkeyPatch()
    engine_aiosqlite.setattr(aiosqlite, "connect", spy)
    try:
        await _open_and_hold()
    finally:
        engine_aiosqlite.undo()

    assert seen, "get_db() did not open a connection through aiosqlite.connect"
    assert all(c.daemon for c in seen), "aiosqlite worker thread is non-daemon — can block interpreter exit"


async def test_normal_use_still_closes_connection(sqlite_path):
    before = _aiosqlite_threads()
    await _open_and_hold()
    leaked = _aiosqlite_threads() - before
    for t in leaked:
        t.join(timeout=2)
    assert not [t for t in leaked if t.is_alive()]
