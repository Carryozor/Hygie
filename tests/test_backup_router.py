"""Endpoint tests for backend/routers/backup.py — previously zero coverage
(found during the 2026-08-10 audit). The backup mechanics themselves
(run_backup/list_backups internals) are covered by tests/test_backup_core.py;
these tests exercise the HTTP wiring: status codes, error mapping, and the
delete endpoint's path-traversal guard.
"""
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _bypass_backup_router_auth(test_client):
    """conftest.py's test_client fixture overrides auth keyed by the
    post-reload backend.auth.require_auth object, but backend.routers.backup
    is never explicitly reloaded there — its routes still carry whatever
    require_auth reference they captured at their own (earlier) import, so
    the global override silently misses them (real 401s). Override using
    the router module's own captured reference instead."""
    import backend.routers.backup as backup_router_mod
    test_client.app.dependency_overrides[backup_router_mod.require_auth] = lambda: "testuser"
    yield
    test_client.app.dependency_overrides.pop(backup_router_mod.require_auth, None)


def test_trigger_backup_returns_filename(test_client):
    with patch("backend.routers.backup.run_backup", return_value="hygie_20260810_100000.db"):
        r = test_client.post("/api/backup")
    assert r.status_code == 200
    assert r.json()["filename"] == "hygie_20260810_100000.db"


def test_trigger_backup_returns_500_when_backup_fails(test_client):
    with patch("backend.routers.backup.run_backup", return_value=None):
        r = test_client.post("/api/backup")
    assert r.status_code == 500


def test_get_backups_returns_list(test_client):
    fake_list = [{"filename": "hygie_1.db", "size_bytes": 10, "created_at": "2026-08-10T00:00:00+00:00"}]
    with (
        patch("backend.routers.backup.list_backups", return_value=fake_list),
        patch("backend.routers.backup.get_setting", return_value="/app/data/backups"),
    ):
        r = test_client.get("/api/backup")
    assert r.status_code == 200
    assert r.json() == fake_list


def test_delete_backup_rejects_path_traversal_with_slash(test_client):
    """ASGI routing normalizes '..' segments before the request even reaches
    our handler (so this never becomes a 200) — the important assertion is
    that no delete can happen via this path, whichever layer blocks it."""
    r = test_client.delete("/api/backup/..%2Fetc%2Fpasswd")
    assert r.status_code != 200


def test_delete_backup_rejects_filename_not_matching_prefix(test_client):
    r = test_client.delete("/api/backup/not_a_backup.db")
    assert r.status_code == 400


def test_delete_backup_rejects_backslash(test_client):
    r = test_client.delete("/api/backup/hygie_..%5C..%5Cetc.db")
    assert r.status_code == 400


def test_delete_backup_returns_404_when_missing(test_client):
    with patch("backend.routers.backup.get_setting", return_value="/tmp/does-not-exist-hygie-backups"):
        r = test_client.delete("/api/backup/hygie_20260810_100000.db")
    assert r.status_code == 404


def test_delete_backup_removes_existing_file(test_client, tmp_path):
    backup_file = tmp_path / "hygie_20260810_100000.db"
    backup_file.write_text("x")

    async def _fake_get_setting(key, default=None):
        return str(tmp_path) if key == "backup_path" else default

    with patch("backend.routers.backup.get_setting", side_effect=_fake_get_setting):
        r = test_client.delete("/api/backup/hygie_20260810_100000.db")

    assert r.status_code == 200
    assert r.json() == {"status": "deleted"}
    assert not backup_file.exists()
