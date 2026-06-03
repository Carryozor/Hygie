"""Schema consistency gate: every DDL column must exist after init_db()."""
import re

import aiosqlite
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def consistent_db(monkeypatch, tmp_path):
    """Temp SQLite DB fully initialised via init_db(), with all module-level
    path constants patched to avoid touching the production database."""
    import backend.db.utils as _utils
    import backend.db.engine as _engine
    import backend.db.schema as _schema

    path = str(tmp_path / "schema_check.db")

    monkeypatch.setattr(_utils, "DB_PATH", path)
    monkeypatch.setattr(_engine, "SQLITE_PATH", path)
    monkeypatch.setattr(_engine, "DIALECT", "sqlite")
    monkeypatch.setattr(_schema, "DB_PATH", path)

    await _schema.init_db()
    return path


@pytest.mark.asyncio
async def test_schema_consistency(consistent_db):
    """Every column declared in schema DDL must exist after init_db() migrations."""
    from backend.db.schema import _TABLES

    errors = []
    async with aiosqlite.connect(consistent_db) as db:
        for table_name, ddl, _ in _TABLES:
            async with db.execute(f"PRAGMA table_info({table_name})") as cur:
                actual = {row[1] for row in await cur.fetchall()}

            declared = re.findall(r"^\s{4}(\w+)\s+\w", ddl, re.MULTILINE)
            skip = {"PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "CREATE", "INDEX"}
            declared = [c for c in declared if c not in skip]

            for col in declared:
                if col not in actual:
                    errors.append(f"{table_name}.{col}: in DDL but missing from DB")

    assert errors == [], (
        "Schema inconsistencies found — add missing columns to expected_cols "
        "or fix the DDL:\n" + "\n".join(f"  - {e}" for e in errors)
    )
