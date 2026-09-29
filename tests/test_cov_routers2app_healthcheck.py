"""Coverage tests for backend/healthcheck.py — the standalone script run by
the Dockerfile's HEALTHCHECK instruction (never imported by the app itself,
hence 0% coverage). Its module-level constants (DATABASE_URL, DB_PATH,
IS_MARIADB) are computed once from env vars at import time, so each test
that needs a different combination reloads the module after setting env.

`main()` always calls sys.exit() — every test wraps the call in
pytest.raises(SystemExit) and asserts the code, mirroring how Docker reads
the HEALTHCHECK exit status.
"""
import importlib
import urllib.error
from unittest.mock import MagicMock, patch

import pytest


def _reload_healthcheck(monkeypatch, database_url="", db_path="/app/data/hygie.db"):
    if database_url:
        monkeypatch.setenv("DATABASE_URL", database_url)
    else:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_PATH", db_path)
    import backend.healthcheck as hc
    importlib.reload(hc)
    return hc


# ─── _http_check ─────────────────────────────────────────────────────────

def test_http_check_returns_status_and_body_on_success(monkeypatch):
    hc = _reload_healthcheck(monkeypatch)
    fake_resp = MagicMock()
    fake_resp.status = 200
    fake_resp.read.return_value = b'{"status":"healthy"}'
    fake_resp.__enter__ = lambda self: fake_resp
    fake_resp.__exit__ = lambda self, *a: False
    with patch.object(hc.urllib.request, "urlopen", return_value=fake_resp):
        code, body = hc._http_check()
    assert code == 200
    assert "healthy" in body


def test_http_check_returns_code_and_body_on_http_error(monkeypatch):
    hc = _reload_healthcheck(monkeypatch)
    err = urllib.error.HTTPError("url", 503, "degraded", None, MagicMock())
    err.read = MagicMock(return_value=b"degraded body")
    with patch.object(hc.urllib.request, "urlopen", side_effect=err):
        code, body = hc._http_check()
    assert code == 503
    assert body == "degraded body"


def test_http_check_handles_unreadable_error_body(monkeypatch):
    hc = _reload_healthcheck(monkeypatch)
    err = urllib.error.HTTPError("url", 500, "err", None, MagicMock())
    err.read = MagicMock(side_effect=OSError("closed"))
    with patch.object(hc.urllib.request, "urlopen", side_effect=err):
        code, body = hc._http_check()
    assert code == 500
    assert body == ""


def test_http_check_returns_zero_on_connection_error(monkeypatch):
    hc = _reload_healthcheck(monkeypatch)
    with patch.object(hc.urllib.request, "urlopen", side_effect=ConnectionRefusedError("refused")):
        code, body = hc._http_check()
    assert code == 0
    assert "refused" in body


# ─── main() — MariaDB dialect ───────────────────────────────────────────────

def test_main_mariadb_healthy_when_http_ok(monkeypatch, capsys):
    hc = _reload_healthcheck(monkeypatch, database_url="mysql+aiomysql://u:p@h:3306/hygie")
    with patch.object(hc, "_http_check", return_value=(200, "ok")):
        with pytest.raises(SystemExit) as exc:
            hc.main()
    assert exc.value.code == 0
    assert "healthy" in capsys.readouterr().out


def test_main_mariadb_unhealthy_on_http_unreachable(monkeypatch, capsys):
    hc = _reload_healthcheck(monkeypatch, database_url="mysql+aiomysql://u:p@h:3306/hygie")
    with patch.object(hc, "_http_check", return_value=(0, "connection refused")):
        with pytest.raises(SystemExit) as exc:
            hc.main()
    assert exc.value.code == 1
    assert "HTTP unreachable" in capsys.readouterr().out


def test_main_mariadb_unhealthy_on_degraded_status(monkeypatch, capsys):
    hc = _reload_healthcheck(monkeypatch, database_url="mysql+aiomysql://u:p@h:3306/hygie")
    with patch.object(hc, "_http_check", return_value=(503, "degraded")):
        with pytest.raises(SystemExit) as exc:
            hc.main()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "degraded" in out


def test_main_mariadb_unhealthy_on_unexpected_http_code(monkeypatch, capsys):
    hc = _reload_healthcheck(monkeypatch, database_url="mysql+aiomysql://u:p@h:3306/hygie")
    with patch.object(hc, "_http_check", return_value=(418, "teapot")):
        with pytest.raises(SystemExit) as exc:
            hc.main()
    assert exc.value.code == 1
    assert "HTTP 418" in capsys.readouterr().out


# ─── main() — SQLite dialect ─────────────────────────────────────────────────

def test_main_sqlite_unhealthy_when_db_file_missing(monkeypatch, tmp_path, capsys):
    missing_db = str(tmp_path / "does_not_exist.db")
    hc = _reload_healthcheck(monkeypatch, db_path=missing_db)
    with patch.object(hc, "_http_check", return_value=(200, "ok")):
        with pytest.raises(SystemExit) as exc:
            hc.main()
    assert exc.value.code == 1
    assert "DB not found" in capsys.readouterr().out


def test_main_sqlite_healthy_with_valid_db_and_enough_disk(monkeypatch, tmp_path, capsys):
    import sqlite3
    db_path = str(tmp_path / "hygie.db")
    conn = sqlite3.connect(db_path)
    for t in ("settings", "users", "libraries", "media_queue", "logs"):
        conn.execute(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    hc = _reload_healthcheck(monkeypatch, db_path=db_path)
    with patch.object(hc, "_http_check", return_value=(200, "ok")), \
         patch.object(hc.shutil, "disk_usage", return_value=(1000, 1000, 500 * 1024 * 1024)):
        with pytest.raises(SystemExit) as exc:
            hc.main()
    assert exc.value.code == 0
    assert "healthy" in capsys.readouterr().out


def test_main_sqlite_unhealthy_on_integrity_check_failure(monkeypatch, tmp_path, capsys):
    import sqlite3
    db_path = str(tmp_path / "corrupt.db")
    conn = sqlite3.connect(db_path)
    for t in ("settings", "users", "libraries", "media_queue", "logs"):
        conn.execute(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    hc = _reload_healthcheck(monkeypatch, db_path=db_path)

    class _FakeCursor:
        def execute(self, sql, *a):
            self._sql = sql
        def fetchone(self):
            if "integrity_check" in self._sql:
                return ("corrupted page 4",)
            return None
        def fetchall(self):
            return [("settings",), ("users",), ("libraries",), ("media_queue",), ("logs",)]

    class _FakeConn:
        def cursor(self):
            return _FakeCursor()
        def close(self):
            pass

    with patch.object(hc, "_http_check", return_value=(200, "ok")), \
         patch("sqlite3.connect", return_value=_FakeConn()), \
         patch.object(hc.shutil, "disk_usage", return_value=(1000, 1000, 500 * 1024 * 1024)):
        with pytest.raises(SystemExit) as exc:
            hc.main()
    assert exc.value.code == 1
    assert "DB integrity" in capsys.readouterr().out


def test_main_sqlite_unhealthy_on_missing_required_tables(monkeypatch, tmp_path, capsys):
    import sqlite3
    db_path = str(tmp_path / "partial.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY)")  # missing the rest
    conn.commit()
    conn.close()

    hc = _reload_healthcheck(monkeypatch, db_path=db_path)
    with patch.object(hc, "_http_check", return_value=(200, "ok")), \
         patch.object(hc.shutil, "disk_usage", return_value=(1000, 1000, 500 * 1024 * 1024)):
        with pytest.raises(SystemExit) as exc:
            hc.main()
    assert exc.value.code == 1
    assert "Missing tables" in capsys.readouterr().out


def test_main_sqlite_unhealthy_on_sqlite_error(monkeypatch, tmp_path, capsys):
    db_path = str(tmp_path / "hygie.db")
    # An empty (zero-byte) file makes sqlite3.connect().execute() raise
    # "file is not a database" — a real sqlite3.Error, not a Python bug.
    with open(db_path, "wb") as f:
        f.write(b"not a real sqlite file")

    hc = _reload_healthcheck(monkeypatch, db_path=db_path)
    with patch.object(hc, "_http_check", return_value=(200, "ok")), \
         patch.object(hc.shutil, "disk_usage", return_value=(1000, 1000, 500 * 1024 * 1024)):
        with pytest.raises(SystemExit) as exc:
            hc.main()
    assert exc.value.code == 1
    assert "DB error" in capsys.readouterr().out


def test_main_sqlite_unhealthy_on_low_disk(monkeypatch, tmp_path, capsys):
    import sqlite3
    db_path = str(tmp_path / "hygie.db")
    conn = sqlite3.connect(db_path)
    for t in ("settings", "users", "libraries", "media_queue", "logs"):
        conn.execute(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    hc = _reload_healthcheck(monkeypatch, db_path=db_path)
    with patch.object(hc, "_http_check", return_value=(200, "ok")), \
         patch.object(hc.shutil, "disk_usage", return_value=(1000, 1000, 10 * 1024 * 1024)):
        with pytest.raises(SystemExit) as exc:
            hc.main()
    assert exc.value.code == 1
    assert "Low disk" in capsys.readouterr().out


def test_main_sqlite_unhealthy_when_disk_check_raises(monkeypatch, tmp_path, capsys):
    import sqlite3
    db_path = str(tmp_path / "hygie.db")
    conn = sqlite3.connect(db_path)
    for t in ("settings", "users", "libraries", "media_queue", "logs"):
        conn.execute(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    hc = _reload_healthcheck(monkeypatch, db_path=db_path)
    with patch.object(hc, "_http_check", return_value=(200, "ok")), \
         patch.object(hc.shutil, "disk_usage", side_effect=OSError("no such device")):
        with pytest.raises(SystemExit) as exc:
            hc.main()
    assert exc.value.code == 1
    assert "Disk check failed" in capsys.readouterr().out
