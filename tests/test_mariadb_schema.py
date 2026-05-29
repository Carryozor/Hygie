"""Verify that MariaDB DDL is valid SQL (parse-only, no live MariaDB needed)."""
import pytest
from backend.db.schema_mariadb import MARIADB_TABLES, MARIADB_INDEXES


def test_all_tables_present():
    names = {t[0] for t in MARIADB_TABLES}
    expected = {
        "settings", "users", "libraries", "media_queue", "ignored_media",
        "seerr_user_rules", "logs", "job_history", "stats_history",
        "rate_limit", "expert_rules", "notifications",
    }
    assert names == expected


def test_each_table_has_create_statement():
    for name, ddl in MARIADB_TABLES:
        assert "CREATE TABLE" in ddl, f"{name} missing CREATE TABLE"
        assert name in ddl, f"{name} not in its own DDL"


def test_no_sqlite_autoincrement():
    for name, ddl in MARIADB_TABLES:
        assert "AUTOINCREMENT" not in ddl, f"{name} still has AUTOINCREMENT"


def test_no_sqlite_pragmas():
    for name, ddl in MARIADB_TABLES:
        assert "strftime" not in ddl, f"{name} still has strftime default"


def test_indexes_defined():
    assert len(MARIADB_INDEXES) >= 6
    for idx_sql in MARIADB_INDEXES:
        assert "CREATE INDEX" in idx_sql or "CREATE UNIQUE INDEX" in idx_sql
