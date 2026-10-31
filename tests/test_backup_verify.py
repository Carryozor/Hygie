"""Restore check (4.5): a dump that exists is not a dump that restores.

2026-10: backups logged "Backup créé" for 27 days while no usable file existed.
The existing check only reads the first gzip byte, which a truncated dump passes.
Here the whole file is read (gzip CRC included) and its content is checked.
"""
import gzip
import os
import sqlite3
from unittest.mock import AsyncMock

import pytest

import backend.backup as backup
from backend import backup_verify as bv

DUMP_HEAD = "-- MariaDB dump 10.19\n"
TABLES = "".join(f"DROP TABLE IF EXISTS `{t}`;\nCREATE TABLE `{t}` (id int);\nINSERT INTO `{t}` VALUES (1);\n"
                 for t in ("settings", "users", "media_queue"))
TRAILER = "-- Dump completed on 2026-10-31 20:58:01\n"


def _gz(path, text):
    with gzip.open(path, "wb") as f:
        f.write(text.encode())
    return str(path)


def _sqlite(path, tables=("settings", "users", "media_queue")):
    con = sqlite3.connect(path)
    for t in tables:
        con.execute(f"CREATE TABLE {t} (id INTEGER)")
        con.execute(f"INSERT INTO {t} VALUES (1)")
    con.commit()
    con.close()
    return str(path)


# ── sqlite ──────────────────────────────────────────────────────────────────

def test_valid_sqlite_backup(tmp_path):
    out = bv.verify_backup_content(_sqlite(tmp_path / "hygie_1.db"))
    assert out["ok"] is True and out["format"] == "sqlite"


def test_sqlite_missing_required_table(tmp_path):
    out = bv.verify_backup_content(_sqlite(tmp_path / "hygie_1.db", tables=("settings",)))
    assert out["ok"] is False and "media_queue" in out["reason"]


def test_sqlite_garbage_file(tmp_path):
    p = tmp_path / "hygie_1.db"
    p.write_bytes(b"not a database" * 100)
    assert bv.verify_backup_content(str(p))["ok"] is False


def test_sqlite_corrupted_pages_fail_integrity_check(tmp_path):
    p = _sqlite(tmp_path / "hygie_1.db")
    with sqlite3.connect(p) as con:
        con.execute("CREATE TABLE big (v BLOB)")
        con.executemany("INSERT INTO big VALUES (?)", [(os.urandom(2000),) for _ in range(50)])
    size = os.path.getsize(p)
    with open(p, "r+b") as f:                       # wreck a page in the middle of the file
        f.seek(size // 2)
        f.write(b"\xff" * 4096)
    assert bv.verify_backup_content(p)["ok"] is False


def test_verification_does_not_modify_the_backup(tmp_path):
    p = _sqlite(tmp_path / "hygie_1.db")
    before = open(p, "rb").read()
    bv.verify_backup_content(p)
    assert open(p, "rb").read() == before
    assert not [f for f in os.listdir(tmp_path) if f.endswith(("-wal", "-shm", "-journal"))]


# ── mariadb dumps ───────────────────────────────────────────────────────────

def test_valid_gzip_dump(tmp_path):
    out = bv.verify_backup_content(_gz(tmp_path / "hygie_1.sql.gz", DUMP_HEAD + TABLES + TRAILER))
    assert out["ok"] is True and out["format"] == "sql"


def test_valid_plain_dump(tmp_path):
    p = tmp_path / "hygie_1.sql"
    p.write_text(DUMP_HEAD + TABLES + TRAILER)
    assert bv.verify_backup_content(str(p))["ok"] is True


def test_dump_without_completion_trailer_is_truncated(tmp_path):
    out = bv.verify_backup_content(_gz(tmp_path / "hygie_1.sql.gz", DUMP_HEAD + TABLES))
    assert out["ok"] is False and "incomplet" in out["reason"]


def test_truncated_gzip_stream_is_detected(tmp_path):
    p = _gz(tmp_path / "hygie_1.sql.gz", DUMP_HEAD + TABLES * 200 + TRAILER)
    raw = open(p, "rb").read()
    open(p, "wb").write(raw[: len(raw) // 2])
    assert bv.verify_backup_content(p)["ok"] is False


def test_dump_missing_a_required_table(tmp_path):
    body = DUMP_HEAD + TABLES.replace("media_queue", "other") + TRAILER
    out = bv.verify_backup_content(_gz(tmp_path / "hygie_1.sql.gz", body))
    assert out["ok"] is False and "media_queue" in out["reason"]


def test_table_name_split_across_read_chunks_is_still_found(tmp_path, monkeypatch):
    monkeypatch.setattr(bv, "_CHUNK", 16)
    out = bv.verify_backup_content(_gz(tmp_path / "hygie_1.sql.gz", DUMP_HEAD + TABLES + TRAILER))
    assert out["ok"] is True


def test_empty_and_missing_files(tmp_path):
    (tmp_path / "hygie_1.sql").write_bytes(b"")
    assert bv.verify_backup_content(str(tmp_path / "hygie_1.sql"))["ok"] is False
    assert bv.verify_backup_content(str(tmp_path / "nope.db"))["ok"] is False


def test_unknown_extension_is_rejected(tmp_path):
    p = tmp_path / "hygie_1.txt"
    p.write_text("x")
    assert bv.verify_backup_content(str(p))["ok"] is False


# ── wiring ──────────────────────────────────────────────────────────────────

@pytest.fixture
def created(tmp_path, monkeypatch):
    async def fake_backup(d, ts):
        _gz(tmp_path / f"hygie_{ts}.sql.gz", DUMP_HEAD + TABLES)        # truncated: no trailer
        return f"hygie_{ts}.sql.gz"
    monkeypatch.setattr(backup, "DIALECT", "mariadb")
    monkeypatch.setattr(backup, "_mariadb_backup", fake_backup)
    log = AsyncMock()
    monkeypatch.setattr(backup, "add_log", log)
    return tmp_path, log


async def test_create_backup_rejects_a_dump_that_cannot_be_restored(created):
    d, log = created
    assert await backup._create_backup(str(d), 7) is None
    assert not list(d.glob("hygie_*.sql.gz"))                 # it must not count as a backup
    assert any("ERROR" == c.args[0] for c in log.await_args_list)


async def test_create_backup_keeps_a_valid_dump(created, monkeypatch):
    d, _ = created

    async def good(dd, ts):
        _gz(d / f"hygie_{ts}.sql.gz", DUMP_HEAD + TABLES + TRAILER)
        return f"hygie_{ts}.sql.gz"
    monkeypatch.setattr(backup, "_mariadb_backup", good)
    name = await backup._create_backup(str(d), 7)
    assert name and (d / name).exists()


def test_verify_endpoint(test_client, monkeypatch, tmp_path):
    import backend.routers.backup as r
    p = _sqlite(tmp_path / "hygie_1.db")
    test_client.app.dependency_overrides[r.require_auth] = lambda: "testuser"
    monkeypatch.setattr(r, "get_setting", AsyncMock(return_value=str(tmp_path)))
    try:
        ok = test_client.post("/api/backup/hygie_1.db/verify")
        bad_name = test_client.post("/api/backup/..%2Fetc%2Fpasswd/verify")
        missing = test_client.post("/api/backup/hygie_nope.db/verify")
    finally:
        test_client.app.dependency_overrides.pop(r.require_auth, None)
    assert ok.status_code == 200 and ok.json()["ok"] is True and p
    assert bad_name.status_code in (400, 404, 405)
    assert missing.status_code == 404


def test_verify_endpoint_requires_auth(test_client):
    assert test_client.post("/api/backup/hygie_1.db/verify").status_code in (401, 403)
