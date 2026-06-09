"""Tests for scan/deletion trigger guards — 409 when job already running."""
import pytest
from unittest.mock import patch


def test_scan_trigger_returns_409_when_running(test_client):
    """POST /api/scan/trigger must return 409 if scan is already running."""
    with patch("backend.routers.scheduler.is_scan_running", return_value=True):
        r = test_client.post("/api/scan/trigger")
    assert r.status_code == 409
    assert "already" in r.json().get("detail", "").lower() or r.json().get("error")


def test_scan_trigger_returns_202_when_idle(test_client):
    """POST /api/scan/trigger must return 2xx if scan is not running."""
    with patch("backend.routers.scheduler.is_scan_running", return_value=False):
        with patch("backend.routers.scheduler.run_scan"):
            r = test_client.post("/api/scan/trigger")
    assert r.status_code in (200, 202)


def test_deletion_trigger_returns_409_when_running(test_client):
    """POST /api/deletion/trigger must return 409 if deletion is already running."""
    with patch("backend.routers.scheduler.is_deletion_running", return_value=True):
        r = test_client.post("/api/deletion/trigger")
    assert r.status_code == 409


def test_scheduler_run_deletion_returns_409_when_running(test_client):
    """POST /api/scheduler/run/deletion must return 409 if deletion is running."""
    with patch("backend.routers.scheduler.is_deletion_running", return_value=True):
        r = test_client.post("/api/scheduler/run/deletion")
    assert r.status_code == 409


def test_scheduler_run_scan_returns_409_when_running(test_client):
    """POST /api/scheduler/run/scan must return 409 if scan is running."""
    with patch("backend.routers.scheduler.is_scan_running", return_value=True):
        r = test_client.post("/api/scheduler/run/scan")
    assert r.status_code == 409
