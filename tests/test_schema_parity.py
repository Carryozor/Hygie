"""Column parity between the SQLite DDL (_TABLES) and the MariaDB DDL
(MARIADB_TABLES).

SQLite gains new columns automatically: schema.py's `_TABLES[*][2]` list
feeds an ALTER-on-migration mechanism (see migrations.py) that the MariaDB
DDL does not mirror — a column added to the SQLite CREATE TABLE statement
silently has no MariaDB equivalent unless a matching mXXX migration is
written by hand (m006, m014 are exactly this fix, applied after the fact).
This test catches that divergence at the column-name level so it fails CI
instead of surfacing as a runtime "Unknown column" error on MariaDB.
"""
import re

from backend.db.schema import _TABLES
from backend.db.schema_mariadb import MARIADB_TABLES

_SKIP = {
    "PRIMARY", "UNIQUE", "FOREIGN", "CONSTRAINT", "KEY", "CHECK",
    "CREATE", "INDEX", "REFERENCES",
}


def _declared_columns(ddl: str) -> set[str]:
    """Extract column names from a CREATE TABLE DDL string."""
    declared = re.findall(r"^\s{4,}(\w+)\s+\w", ddl, re.MULTILINE)
    return {c for c in declared if c.upper() not in _SKIP}


def test_media_queue_columns_match_between_dialects():
    sqlite_ddl = next(ddl for name, ddl, _ in _TABLES if name == "media_queue")
    mariadb_ddl = next(ddl for name, ddl, *_ in MARIADB_TABLES if name == "media_queue")
    sqlite_cols = _declared_columns(sqlite_ddl)
    mariadb_cols = _declared_columns(mariadb_ddl)
    missing_from_mariadb = sqlite_cols - mariadb_cols
    missing_from_sqlite = mariadb_cols - sqlite_cols
    assert not missing_from_mariadb, f"Columns in SQLite but missing from MariaDB DDL: {missing_from_mariadb}"
    assert not missing_from_sqlite, f"Columns in MariaDB but missing from SQLite DDL: {missing_from_sqlite}"


def test_all_shared_tables_have_matching_columns():
    """Same check for every table defined in both dialects, not just media_queue."""
    sqlite_tables = {name: ddl for name, ddl, *_ in _TABLES}
    mariadb_tables = {name: ddl for name, ddl, *_ in MARIADB_TABLES}
    shared = set(sqlite_tables) & set(mariadb_tables)
    assert shared, "expected at least one table defined in both dialects"

    failures = []
    for table in sorted(shared):
        sqlite_cols = _declared_columns(sqlite_tables[table])
        mariadb_cols = _declared_columns(mariadb_tables[table])
        diff_mariadb = sqlite_cols - mariadb_cols
        diff_sqlite = mariadb_cols - sqlite_cols
        if diff_mariadb:
            failures.append(f"{table}: missing from MariaDB DDL: {diff_mariadb}")
        if diff_sqlite:
            failures.append(f"{table}: missing from SQLite DDL: {diff_sqlite}")

    assert not failures, "Schema column divergence:\n" + "\n".join(failures)
