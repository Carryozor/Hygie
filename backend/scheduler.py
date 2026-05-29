# backend/scheduler.py
"""Thin orchestrator — imports jobs, re-exports public symbols for backward compatibility."""
from ._job_state import _scan_lock, _deletion_lock, is_scan_running, is_deletion_running
from .scanner import run_scan, run_scan_library, reevaluate_library_queue
from .deletion import run_deletion, run_ignored_cleanup
from .collection import sync_emby_collection
from .plex_collection import sync_plex_overlays

__all__ = [
    "_scan_lock", "_deletion_lock", "is_scan_running", "is_deletion_running",
    "run_scan", "run_scan_library", "reevaluate_library_queue",
    "run_deletion", "run_ignored_cleanup",
    "sync_emby_collection", "sync_plex_overlays",
]
