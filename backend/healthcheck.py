#!/usr/bin/env python3
"""
Standalone healthcheck for Docker.

Verifies:
  - HTTP API responds
  - DB file exists and has required tables
  - DB is writable and not corrupted
  - Disk has > 50 MB free

Exit 0 = healthy, 1 = unhealthy.
"""
import os
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request


DB_PATH = os.environ.get("DB_PATH", "/app/data/hygie.db")
HEALTH_URL = "http://127.0.0.1:8000/health"
MIN_FREE_MB = 50


def _http_check() -> tuple[int, str]:
    try:
        req = urllib.request.Request(HEALTH_URL)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read(2048).decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        # /health returns 503 when degraded but server is alive
        try:
            body = e.read(2048).decode("utf-8", errors="ignore")
        except Exception:
            body = ""
        return e.code, body
    except Exception as e:
        return 0, str(e)


def main():
    fails = []

    # 1. HTTP
    code, body = _http_check()
    if code == 0:
        fails.append(f"HTTP unreachable: {body}")
    elif code not in (200, 401):  # 401 = auth required (means API is alive)
        fails.append(f"HTTP {code}")

    # 2. DB file exists
    if not os.path.exists(DB_PATH):
        fails.append(f"DB not found at {DB_PATH}")
    else:
        # 3. DB integrity + required tables
        try:
            conn = sqlite3.connect(DB_PATH, timeout=2)
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check")
            result = cur.fetchone()
            if result and result[0] != "ok":
                fails.append(f"DB integrity: {result[0]}")
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cur.fetchall()}
            required = {"settings", "users", "libraries", "media_queue", "logs"}
            missing = required - tables
            if missing:
                fails.append(f"Missing tables: {missing}")
            # Writable check
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("ROLLBACK")
            conn.close()
        except sqlite3.Error as e:
            fails.append(f"DB error: {e}")

    # 4. Disk space
    try:
        _, _, free = shutil.disk_usage(os.path.dirname(DB_PATH) or "/")
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
