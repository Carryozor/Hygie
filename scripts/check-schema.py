#!/usr/bin/env python3
"""CI gate: ensure every column declared in schema DDL exists after init_db().

Usage:
    python3 scripts/check-schema.py

Exit codes:
    0 — all good
    1 — schema inconsistencies detected
"""
import asyncio
import re
import sys
import tempfile
import os

# Make imports work from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def check() -> list[str]:
    import aiosqlite

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_path = f.name

    errors = []
    try:
        # Import modules and patch the path attributes directly (same technique
        # as test_schema_migration.py) so that the already-loaded module-level
        # constants point to our temp file, not the production DB.
        import backend.db.utils as _utils
        import backend.db.engine as _engine
        import backend.db.schema as _schema

        _utils.DB_PATH = tmp_path
        _engine.SQLITE_PATH = tmp_path
        # Ensure DIALECT stays sqlite (no DATABASE_URL set in CI)
        _engine.DIALECT = "sqlite"

        # Also patch the name that schema.py imported at load time
        _schema.DB_PATH = tmp_path

        await _schema.init_db()

        async with aiosqlite.connect(tmp_path) as db:
            for table_name, ddl, _ in _schema._TABLES:
                async with db.execute(f"PRAGMA table_info({table_name})") as cur:
                    actual = {row[1] for row in await cur.fetchall()}

                # Extract column names from DDL (first word of each indented line)
                declared = re.findall(r"^\s{4}(\w+)\s+\w", ddl, re.MULTILINE)
                # Filter out SQL keywords that aren't column names
                skip = {"PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "CREATE", "INDEX"}
                declared = [c for c in declared if c not in skip]

                for col in declared:
                    if col not in actual:
                        errors.append(f"  {table_name}.{col}: in DDL but missing from DB")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    return errors


def main():
    errors = asyncio.run(check())
    if errors:
        print("Schema inconsistencies detected:")
        for e in errors:
            print(e)
        sys.exit(1)
    print("Schema consistent — all DDL columns present after init_db()")


if __name__ == "__main__":
    main()
