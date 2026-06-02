# backend/scanner/__init__.py
"""scanner package — public API.

All callers use: from .scanner import run_scan, run_scan_library, reevaluate_library_queue
This package replaces the old scanner.py monolith.
"""
from ._orchestrator import run_scan, run_scan_library, run_scan_libraries
from ._emby_scanner import reevaluate_library_queue

__all__ = ["run_scan", "run_scan_library", "run_scan_libraries", "reevaluate_library_queue"]
