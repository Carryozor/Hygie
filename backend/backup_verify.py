"""Content check of a backup file: does it look restorable?

A dump that exists is not a dump that restores. The 2026-10 incident showed
"Backup créé" logs for 27 days without a usable file, and a gzip header check
passes a truncated dump. Here the whole file is read (so gzip's CRC is
verified), the dump must carry mysqldump's completion trailer, and the tables
Hygie cannot live without must be present. Nothing is ever written: SQLite
files are opened read-only and immutable.
"""
import gzip
import logging
import re
import sqlite3
import zlib
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)

REQUIRED_TABLES = ("settings", "users", "media_queue")
_CHUNK = 1 << 20
_TRAILER = b"-- Dump completed"
_TAIL = 4096
_CREATE = {t: re.compile(rb"CREATE TABLE (?:IF NOT EXISTS )?[`\"]?" + t.encode() + rb"[`\"]?\b") for t in REQUIRED_TABLES}
_OVERLAP = 64


def _result(ok: bool, fmt: str, reason: str = "") -> dict:
    return {"ok": ok, "format": fmt, "reason": reason or None}


def _verify_sqlite(path: str) -> dict:
    uri = f"file:{quote(path)}?mode=ro&immutable=1"
    try:
        con = sqlite3.connect(uri, uri=True)
        try:
            check = con.execute("PRAGMA integrity_check").fetchall()
            if check != [("ok",)]:
                return _result(False, "sqlite", f"intégrité SQLite en échec : {check[0][0]}")
            present = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()
    except sqlite3.Error as e:
        return _result(False, "sqlite", f"base illisible : {e}")
    missing = [t for t in REQUIRED_TABLES if t not in present]
    if missing:
        return _result(False, "sqlite", f"table(s) absente(s) : {', '.join(missing)}")
    return _result(True, "sqlite")


def _verify_sql(path: str) -> dict:
    opener = gzip.open if path.endswith(".gz") else open
    found, tail, carry = set(), b"", b""
    try:
        with opener(path, "rb") as f:
            while chunk := f.read(_CHUNK):
                window = carry + chunk
                for name, rx in _CREATE.items():
                    if name not in found and rx.search(window):
                        found.add(name)
                carry = window[-_OVERLAP:]
                tail = (tail + chunk)[-_TAIL:]
    except (OSError, EOFError, zlib.error) as e:
        return _result(False, "sql", f"fichier tronqué ou illisible : {e}")
    if _TRAILER not in tail:
        return _result(False, "sql", "dump incomplet : ligne de fin 'Dump completed' absente")
    missing = [t for t in REQUIRED_TABLES if t not in found]
    if missing:
        return _result(False, "sql", f"table(s) absente(s) : {', '.join(missing)}")
    return _result(True, "sql")


def verify_backup_content(path: str) -> dict:
    """{'ok': bool, 'format': 'sqlite'|'sql'|'unknown', 'reason': str|None}. Blocking."""
    p = Path(path)
    try:
        if not p.is_file() or p.stat().st_size <= 0:
            return _result(False, "unknown", "fichier absent ou vide")
    except OSError as e:
        return _result(False, "unknown", f"fichier inaccessible : {e}")
    if p.name.endswith(".db"):
        return _verify_sqlite(str(p))
    if p.name.endswith((".sql", ".sql.gz")):
        return _verify_sql(str(p))
    return _result(False, "unknown", "extension de sauvegarde inconnue")
