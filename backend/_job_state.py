"""Shared job locks for scan and deletion jobs.

Kept separate from scanner.py and deletion.py to avoid circular imports:
both modules need the locks, and scheduler.py orchestrates both.

The actual lock implementation is provided by _lock_backend.py, which
supports both in-process (asyncio) and cross-process (MariaDB advisory)
modes. Set HYGIE_LOCK_BACKEND=mariadb to enable cross-process mode.
"""
from ._lock_backend import scan_lock as _scan_lock, deletion_lock as _deletion_lock


def is_scan_running() -> bool:
    return _scan_lock.locked()


def is_deletion_running() -> bool:
    return _deletion_lock.locked()
