#!/usr/bin/env python3
"""
Standalone healthcheck for Docker HEALTHCHECK instruction.

Dialect-aware:
  - SQLite (default)  : checks file existence, integrity, required tables, disk
  - MariaDB           : skips file/sqlite3 checks; relies on HTTP /health endpoint

Exit 0 = healthy, 1 = unhealthy.
"""
import os
import sys

# Python adds the script's directory (/app/backend/) to sys.path[0] when running
# a script directly. This shadows the stdlib 'types' module with backend/types.py,
# breaking any import that transitively imports 'types' (shutil, re, enum, ...).
# Remove the script's own directory before importing anything else.
_here = os.path.dirname(os.path.abspath(__file__))
sys.path = [p for p in sys.path if os.path.abspath(p) != _here]

import shutil
import urllib.error
import urllib.request

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DB_PATH      = os.environ.get("DB_PATH", "/app/data/hygie.db")
HEALTH_URL   = "http://127.0.0.1:8000/health"
MIN_FREE_MB  = 50
IS_MARIADB   = bool(DATABASE_URL)


def _http_check() -> tuple[int, str]:
    try:
        req = urllib.request.Request(HEALTH_URL)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read(2048).decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        try:
            body = e.read(2048).decode("utf-8", errors="ignore")
        except Exception:
            body = ""
        return e.code, body
    except Exception as e:
        return 0, str(e)


def main():
    fails = []

    # ── HTTP check (always performed) ────────────────────────────────────────
    code, body = _http_check()
    if code == 0:
        fails.append(f"HTTP unreachable: {body}")
    elif code not in (200, 401, 503):
        # 401 = auth required (API alive), 503 = degraded (API alive but warns)
        fails.append(f"HTTP {code}")

    if IS_MARIADB:
        # MariaDB: the HTTP /health endpoint covers DB connectivity via get_db().
        # SQLite-specific checks (file, sqlite3, PRAGMA) would be wrong here.
        # The /health endpoint already uses the dialect-aware health logic.
        if code == 0:
            # API unreachable — already added above, nothing more to do
            pass
        elif code == 503:
            # Degraded signal from the /health endpoint
            fails.append("App reports degraded status (see /health for details)")
        # Skip disk check for /app/data if using external MariaDB
    else:
        # ── SQLite-specific checks ────────────────────────────────────────────
        import sqlite3

        if not os.path.exists(DB_PATH):
            fails.append(f"DB not found at {DB_PATH}")
        else:
            try:
                conn = sqlite3.connect(DB_PATH, timeout=2)
                cur  = conn.cursor()
                cur.execute("PRAGMA integrity_check")
                result = cur.fetchone()
                if result and result[0] != "ok":
                    fails.append(f"DB integrity: {result[0]}")
                cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables   = {row[0] for row in cur.fetchall()}
                required = {"settings", "users", "libraries", "media_queue", "logs"}
                missing  = required - tables
                if missing:
                    fails.append(f"Missing tables: {missing}")
                cur.execute("BEGIN IMMEDIATE")
                cur.execute("ROLLBACK")
                conn.close()
            except sqlite3.Error as e:
                fails.append(f"DB error: {e}")

        # Disk check
        try:
            disk_dir = os.path.dirname(DB_PATH) or "/"
            _, _, free = shutil.disk_usage(disk_dir)
            if free // (1024 * 1024) < MIN_FREE_MB:
                fails.append(f"Low disk: {free // (1024 * 1024)} MB free")
        except Exception as e:
            fails.append(f"Disk check failed: {e}")

    if fails:
        print("UNHEALTHY: " + "; ".join(fails))
        sys.exit(1)

    print("healthy")
    sys.exit(0)


if __name__ == "__main__":
    main()
