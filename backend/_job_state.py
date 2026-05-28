"""Shared asyncio locks for scan and deletion jobs.

Kept separate from scanner.py and deletion.py to avoid circular imports:
both modules need the locks, and scheduler.py orchestrates both.
"""
import asyncio

_scan_lock = asyncio.Lock()
_deletion_lock = asyncio.Lock()


def is_scan_running() -> bool:
    return _scan_lock.locked()


def is_deletion_running() -> bool:
    return _deletion_lock.locked()
