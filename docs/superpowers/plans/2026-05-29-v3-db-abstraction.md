# Hygie v3.0 — Phase 1: Database Abstraction (SQLite + MariaDB)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add MariaDB as an optional database backend alongside SQLite, via a thin `DbConn` abstraction layer that all existing DB code migrates to.

**Architecture:** Introduce `backend/db/engine.py` with a `DbConn` class that wraps either `aiosqlite` (SQLite, default) or `aiomysql` (MariaDB). All 24 files that currently call `aiosqlite.connect(DB_PATH)` are migrated to `async with get_db() as db:`. SQLite keeps existing behavior (WAL, `?` params); MariaDB uses `%s` params and a separate DDL file. A one-way CLI migration script copies an existing SQLite database to MariaDB.

**Tech Stack:** Python 3.12, aiosqlite (existing), aiomysql 0.2.0, Docker Compose profiles (MariaDB 11 service), pytest-asyncio

---

## File Structure

**New files:**
- `backend/db/engine.py` — `DbConn` class, `get_db()` context manager, pool lifecycle
- `backend/db/schema_mariadb.py` — MariaDB DDL for all 12 tables, `init_db_mariadb()`
- `backend/tools/__init__.py` — empty, makes tools a package
- `backend/tools/migrate_to_mariadb.py` — CLI: read SQLite → write MariaDB
- `tests/test_db_engine.py` — tests for DbConn in SQLite mode
- `tests/test_mariadb_schema.py` — tests for schema init with MariaDB mock

**Modified files:**
- `backend/db/schema.py` — dispatch `init_db()` to SQLite or MariaDB impl
- `backend/db/repositories.py` — replace `aiosqlite.connect()` with `get_db()`
- `backend/db/settings_store.py` — same
- `backend/db/logs.py` — same
- `backend/db/media_servers.py` — same
- `backend/db/websocket.py` — same
- `backend/auth.py` — same
- `backend/scanner.py` — same
- `backend/deletion.py` — same
- `backend/notifications.py` — same
- `backend/conditions.py` — same
- `backend/collection.py` — same
- `backend/discord_client.py` — same
- `backend/arr_clients/seerr.py` — same
- `backend/routers/stats.py`, `storage.py`, `metrics.py`, `logs.py`, `media.py`,
  `libraries.py`, `ignored.py`, `calendar.py`, `seerr_rules.py`, `unmonitored.py` — same
- `backend/main.py` — call `await init_db_pool()` / `await close_db_pool()` in lifespan
- `docker-compose.yml` — add MariaDB service under `profiles: [mariadb]`
- `requirements.txt` — add `aiomysql==0.2.0`
- `README.md` — add DB choice guidance

---

### Task 1: `backend/db/engine.py` — DbConn abstraction + get_db()

**Files:**
- Create: `backend/db/engine.py`
- Test: `tests/test_db_engine.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_db_engine.py
"""Tests for DbConn SQLite mode (MariaDB mode needs a real server — skipped here)."""
import os
import pytest
import pytest_asyncio

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.pop("DATABASE_URL", None)  # force SQLite mode

@pytest_asyncio.fixture
async def db(tmp_path):
    import aiosqlite
    from backend.db.engine import get_db, DIALECT
    assert DIALECT == "sqlite"
    # bootstrap a tiny table for testing
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, val TEXT)")
    await conn.commit()
    await conn.close()

    import backend.db.engine as eng
    orig = eng.SQLITE_PATH
    eng.SQLITE_PATH = str(tmp_path / "test.db")
    yield
    eng.SQLITE_PATH = orig


@pytest.mark.asyncio
async def test_execute_insert_and_fetch_one(db):
    from backend.db.engine import get_db
    async with get_db() as conn:
        last_id = await conn.execute("INSERT INTO t (val) VALUES (?)", ("hello",))
        await conn.commit()
        row = await conn.fetch_one("SELECT * FROM t WHERE id=?", (last_id,))
    assert row is not None
    assert row["val"] == "hello"


@pytest.mark.asyncio
async def test_fetch_all(db):
    from backend.db.engine import get_db
    async with get_db() as conn:
        await conn.execute("INSERT INTO t (val) VALUES (?)", ("a",))
        await conn.execute("INSERT INTO t (val) VALUES (?)", ("b",))
        await conn.commit()
        rows = await conn.fetch_all("SELECT val FROM t ORDER BY id")
    assert [r["val"] for r in rows] == ["a", "b"]


@pytest.mark.asyncio
async def test_executemany(db):
    from backend.db.engine import get_db
    async with get_db() as conn:
        await conn.executemany("INSERT INTO t (val) VALUES (?)", [("x",), ("y",), ("z",)])
        await conn.commit()
        rows = await conn.fetch_all("SELECT val FROM t ORDER BY id")
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_rowcount_after_update(db):
    from backend.db.engine import get_db
    async with get_db() as conn:
        await conn.execute("INSERT INTO t (val) VALUES (?)", ("old",))
        await conn.commit()
        rowcount = await conn.execute_write("UPDATE t SET val=? WHERE val=?", ("new", "old"))
    assert rowcount == 1


@pytest.mark.asyncio
async def test_fetch_one_missing_returns_none(db):
    from backend.db.engine import get_db
    async with get_db() as conn:
        row = await conn.fetch_one("SELECT * FROM t WHERE id=?", (999,))
    assert row is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /opt/claude/hygie && python -m pytest tests/test_db_engine.py -v 2>&1 | head -20
```
Expected: `ModuleNotFoundError: No module named 'backend.db.engine'`

- [ ] **Step 3: Create `backend/db/engine.py`**

```python
# backend/db/engine.py
"""Database engine: SQLite (default) or MariaDB via DATABASE_URL env var.

Set DATABASE_URL=mysql+aiomysql://user:pass@host:3306/hygie to use MariaDB.
Leave unset (or empty) to use SQLite at DB_PATH.
"""
import os
import logging
from contextlib import asynccontextmanager
from typing import Any

logger = logging.getLogger(__name__)

DATABASE_URL: str = os.environ.get("DATABASE_URL", "").strip()
SQLITE_PATH: str = os.environ.get("DB_PATH", "/app/data/hygie.db")

DIALECT: str = "mariadb" if DATABASE_URL.startswith(("mysql+", "mariadb+", "mysql://", "mariadb://")) else "sqlite"

# MariaDB connection pool (None when using SQLite)
_pool: Any = None


def _parse_mariadb_url(url: str) -> dict:
    """Parse mysql+aiomysql://user:pass@host:3306/dbname into aiomysql kwargs."""
    # Strip driver prefix
    clean = url
    for prefix in ("mysql+aiomysql://", "mariadb+aiomysql://", "mysql://", "mariadb://"):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
            break
    user_pass, rest = clean.split("@", 1)
    user, password = (user_pass.split(":", 1) if ":" in user_pass else (user_pass, ""))
    host_port_db = rest
    if "/" in host_port_db:
        host_port, db = host_port_db.rsplit("/", 1)
    else:
        host_port, db = host_port_db, "hygie"
    host, port = (host_port.rsplit(":", 1) if ":" in host_port else (host_port, "3306"))
    return {"host": host, "port": int(port), "user": user, "password": password, "db": db}


async def init_db_pool() -> None:
    """Initialize connection pool (no-op for SQLite)."""
    global _pool
    if DIALECT != "mariadb":
        return
    import aiomysql
    kwargs = _parse_mariadb_url(DATABASE_URL)
    _pool = await aiomysql.create_pool(
        minsize=1, maxsize=10,
        autocommit=False,
        charset="utf8mb4",
        **kwargs,
    )
    logger.info("MariaDB pool initialized: %s@%s:%s/%s", kwargs["user"], kwargs["host"], kwargs["port"], kwargs["db"])


async def close_db_pool() -> None:
    """Close pool (no-op for SQLite)."""
    global _pool
    if _pool is not None:
        _pool.close()
        await _pool.wait_closed()
        _pool = None


class DbConn:
    """Unified async DB connection for SQLite and MariaDB.

    API surface:
        fetch_all(sql, params) → list[dict]
        fetch_one(sql, params) → dict | None
        execute(sql, params)   → last insert id (int)
        execute_write(sql, params) → rowcount (int)
        executemany(sql, params_seq)
        commit()
    """

    def __init__(self, raw, dialect: str) -> None:
        self._raw = raw
        self._dialect = dialect

    def _q(self, sql: str) -> str:
        """Translate ? placeholders to %s for MariaDB."""
        if self._dialect == "mariadb":
            return sql.replace("?", "%s")
        return sql

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        if self._dialect == "sqlite":
            self._raw.row_factory = _sqlite_row_factory
            async with self._raw.execute(sql, params) as cur:
                rows = await cur.fetchall()
            return [dict(r) for r in rows]
        else:
            import aiomysql
            async with self._raw.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(self._q(sql), params or ())
                return list(await cur.fetchall())

    async def fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = await self.fetch_all(sql, params)
        return rows[0] if rows else None

    async def execute(self, sql: str, params: tuple = ()) -> int:
        """Run INSERT/UPDATE/DELETE. Returns last insert id."""
        if self._dialect == "sqlite":
            cur = await self._raw.execute(sql, params)
            return cur.lastrowid or 0
        else:
            async with self._raw.cursor() as cur:
                await cur.execute(self._q(sql), params or ())
                return cur.lastrowid or 0

    async def execute_write(self, sql: str, params: tuple = ()) -> int:
        """Run UPDATE/DELETE. Returns rowcount."""
        if self._dialect == "sqlite":
            cur = await self._raw.execute(sql, params)
            return cur.rowcount
        else:
            async with self._raw.cursor() as cur:
                await cur.execute(self._q(sql), params or ())
                return cur.rowcount

    async def executemany(self, sql: str, params_seq) -> None:
        if self._dialect == "sqlite":
            await self._raw.executemany(sql, params_seq)
        else:
            async with self._raw.cursor() as cur:
                await cur.executemany(self._q(sql), list(params_seq))

    async def commit(self) -> None:
        await self._raw.commit()

    async def table_columns(self, table: str) -> set[str]:
        """Return column names for a table (dialect-aware)."""
        if self._dialect == "sqlite":
            async with self._raw.execute(f"PRAGMA table_info({table})") as cur:
                rows = await cur.fetchall()
            return {r[1] for r in rows}
        else:
            rows = await self.fetch_all(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=?", (table,)
            )
            return {r["COLUMN_NAME"] for r in rows}

    async def table_exists(self, table: str) -> bool:
        if self._dialect == "sqlite":
            row = await self.fetch_one(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
            )
            return row is not None
        else:
            row = await self.fetch_one(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=?", (table,)
            )
            return row is not None


def _sqlite_row_factory(cursor, row):
    """sqlite3 row_factory → dict."""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


@asynccontextmanager
async def get_db():
    """Async context manager yielding a DbConn for the configured dialect."""
    if DIALECT == "sqlite":
        import aiosqlite
        async with aiosqlite.connect(SQLITE_PATH) as raw:
            await raw.execute("PRAGMA journal_mode=WAL")
            await raw.execute("PRAGMA foreign_keys=ON")
            yield DbConn(raw, "sqlite")
    else:
        if _pool is None:
            raise RuntimeError("MariaDB pool not initialized — call init_db_pool() at startup")
        async with _pool.acquire() as raw:
            await raw.autocommit(False)
            yield DbConn(raw, "mariadb")
```

- [ ] **Step 4: Run tests**

```bash
cd /opt/claude/hygie && python -m pytest tests/test_db_engine.py -v
```
Expected: 6 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/db/engine.py tests/test_db_engine.py
git commit -m "feat(db): add DbConn abstraction for SQLite/MariaDB dual-dialect support"
```

---

### Task 2: `backend/db/schema_mariadb.py` — MariaDB DDL

**Files:**
- Create: `backend/db/schema_mariadb.py`
- Test: `tests/test_mariadb_schema.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_mariadb_schema.py
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
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /opt/claude/hygie && python -m pytest tests/test_mariadb_schema.py -v 2>&1 | head -10
```
Expected: `ModuleNotFoundError: No module named 'backend.db.schema_mariadb'`

- [ ] **Step 3: Create `backend/db/schema_mariadb.py`**

```python
# backend/db/schema_mariadb.py
"""MariaDB DDL for all Hygie tables.

Key differences from SQLite DDL:
- INTEGER PRIMARY KEY AUTOINCREMENT → INT NOT NULL AUTO_INCREMENT, PRIMARY KEY (id)
- TEXT PRIMARY KEY → VARCHAR(255) PRIMARY KEY  (MySQL can't index unbounded TEXT as PK)
- DEFAULT (strftime(...)) → application provides timestamp; column DEFAULT NULL or omitted
- REAL → DOUBLE
- PRAGMA journal_mode/foreign_keys → handled at connection level by aiomysql
- All tables use ENGINE=InnoDB CHARSET=utf8mb4 for full Unicode + FK support
"""

MARIADB_TABLES: list[tuple[str, str]] = [
    (
        "settings",
        """CREATE TABLE IF NOT EXISTS settings (
            `key`  VARCHAR(255) NOT NULL,
            value  LONGTEXT     NOT NULL,
            PRIMARY KEY (`key`)
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "users",
        """CREATE TABLE IF NOT EXISTS users (
            id            INT          NOT NULL AUTO_INCREMENT,
            username      VARCHAR(255) NOT NULL,
            password_hash TEXT         NOT NULL,
            created_at    VARCHAR(32)  NOT NULL,
            PRIMARY KEY (id),
            UNIQUE KEY uq_users_username (username)
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "libraries",
        """CREATE TABLE IF NOT EXISTS libraries (
            id              VARCHAR(255) NOT NULL,
            name            TEXT         NOT NULL,
            emby_library_id TEXT         NOT NULL,
            conditions      LONGTEXT     NOT NULL DEFAULT ('[]'),
            logic           VARCHAR(10)  NOT NULL DEFAULT 'AND',
            grace_days      INT          NOT NULL DEFAULT 7,
            seerr_conditions LONGTEXT   NOT NULL DEFAULT ('[]'),
            enabled         TINYINT      NOT NULL DEFAULT 1,
            created_at      VARCHAR(32)  DEFAULT NULL,
            server_id       VARCHAR(255) DEFAULT '0',
            deletion_unit   VARCHAR(20)  NOT NULL DEFAULT 'episode',
            PRIMARY KEY (id)
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "media_queue",
        """CREATE TABLE IF NOT EXISTS media_queue (
            id                  INT          NOT NULL AUTO_INCREMENT,
            emby_id             VARCHAR(255) NOT NULL,
            title               TEXT         NOT NULL,
            media_type          TEXT         NOT NULL,
            library_id          VARCHAR(255) NOT NULL,
            library_name        TEXT         NOT NULL,
            file_path           TEXT         NOT NULL,
            poster_url          TEXT         DEFAULT '',
            tmdb_id             VARCHAR(64)  DEFAULT '',
            seerr_id            INT          DEFAULT NULL,
            seerr_user_id       INT          DEFAULT NULL,
            seerr_username      TEXT         DEFAULT '',
            seerr_request_url   TEXT         DEFAULT '',
            radarr_id           INT          DEFAULT NULL,
            sonarr_id           INT          DEFAULT NULL,
            detected_at         VARCHAR(32)  NOT NULL,
            delete_at           VARCHAR(32)  NOT NULL,
            added_date          VARCHAR(32)  DEFAULT NULL,
            last_played         VARCHAR(32)  DEFAULT NULL,
            status              VARCHAR(20)  NOT NULL DEFAULT 'pending',
            notified_30d        TINYINT      DEFAULT 0,
            notified_7d         TINYINT      DEFAULT 0,
            notified_1d         TINYINT      DEFAULT 0,
            notified_now        TINYINT      DEFAULT 0,
            notified_detected   TINYINT      DEFAULT 0,
            notified_thresholds LONGTEXT     DEFAULT ('[]'),
            sonarr_series_id    INT          DEFAULT NULL,
            season_number       INT          DEFAULT NULL,
            PRIMARY KEY (id),
            UNIQUE KEY uq_mq_emby_id (emby_id)
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "ignored_media",
        """CREATE TABLE IF NOT EXISTS ignored_media (
            id               INT          NOT NULL AUTO_INCREMENT,
            emby_id          VARCHAR(255) NOT NULL,
            title            TEXT         NOT NULL,
            media_type       TEXT         DEFAULT 'Movie',
            library_id       VARCHAR(255) DEFAULT '',
            library_name     TEXT         DEFAULT '',
            file_path        TEXT         DEFAULT '',
            poster_url       TEXT         DEFAULT '',
            tmdb_id          VARCHAR(64)  DEFAULT '',
            seerr_id         INT          DEFAULT NULL,
            seerr_user_id    INT          DEFAULT NULL,
            seerr_username   TEXT         DEFAULT '',
            seerr_request_url TEXT        DEFAULT '',
            radarr_id        INT          DEFAULT NULL,
            sonarr_id        INT          DEFAULT NULL,
            added_date       VARCHAR(32)  DEFAULT NULL,
            last_played      VARCHAR(32)  DEFAULT NULL,
            reason           TEXT         DEFAULT '',
            ignored_at       VARCHAR(32)  NOT NULL,
            expire_at        VARCHAR(32)  DEFAULT NULL,
            PRIMARY KEY (id),
            UNIQUE KEY uq_im_emby_id (emby_id)
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "seerr_user_rules",
        """CREATE TABLE IF NOT EXISTS seerr_user_rules (
            id             INT          NOT NULL AUTO_INCREMENT,
            seerr_user_id  INT          NOT NULL,
            seerr_username VARCHAR(255) NOT NULL,
            library_id     VARCHAR(255) NOT NULL,
            grace_days     INT          NOT NULL DEFAULT 30,
            enabled        TINYINT      NOT NULL DEFAULT 1,
            discord_id     VARCHAR(255) DEFAULT '',
            created_at     VARCHAR(32)  DEFAULT NULL,
            PRIMARY KEY (id)
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "logs",
        """CREATE TABLE IF NOT EXISTS logs (
            id      INT         NOT NULL AUTO_INCREMENT,
            ts      VARCHAR(32) NOT NULL,
            level   VARCHAR(10) NOT NULL,
            source  VARCHAR(64) NOT NULL,
            message LONGTEXT    NOT NULL,
            PRIMARY KEY (id)
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "job_history",
        """CREATE TABLE IF NOT EXISTS job_history (
            id          INT         NOT NULL AUTO_INCREMENT,
            job_type    VARCHAR(64) NOT NULL,
            started_at  VARCHAR(32) NOT NULL,
            finished_at VARCHAR(32) DEFAULT NULL,
            status      VARCHAR(20) DEFAULT NULL,
            message     LONGTEXT    DEFAULT NULL,
            PRIMARY KEY (id)
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "stats_history",
        """CREATE TABLE IF NOT EXISTS stats_history (
            id               INT         NOT NULL AUTO_INCREMENT,
            ts               VARCHAR(32) NOT NULL,
            total_deleted    INT         DEFAULT 0,
            total_scanned    INT         DEFAULT 0,
            space_freed_bytes BIGINT     DEFAULT 0,
            month            VARCHAR(7)  NOT NULL,
            library_id       VARCHAR(255) DEFAULT NULL,
            PRIMARY KEY (id)
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "rate_limit",
        """CREATE TABLE IF NOT EXISTS rate_limit (
            `key` VARCHAR(255) NOT NULL,
            ts    DOUBLE       NOT NULL
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "expert_rules",
        """CREATE TABLE IF NOT EXISTS expert_rules (
            id         INT          NOT NULL AUTO_INCREMENT,
            name       TEXT         NOT NULL,
            library_id INT          DEFAULT NULL,
            conditions LONGTEXT     NOT NULL DEFAULT ('[]'),
            operator   VARCHAR(10)  NOT NULL DEFAULT 'AND',
            action     VARCHAR(20)  NOT NULL DEFAULT 'queue',
            enabled    TINYINT      NOT NULL DEFAULT 1,
            priority   INT          NOT NULL DEFAULT 0,
            created_at VARCHAR(32)  DEFAULT NULL,
            PRIMARY KEY (id)
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "notifications",
        """CREATE TABLE IF NOT EXISTS notifications (
            id       INT         NOT NULL AUTO_INCREMENT,
            media_id INT         NOT NULL,
            threshold VARCHAR(20) NOT NULL,
            sent_at  VARCHAR(32) DEFAULT NULL,
            PRIMARY KEY (id),
            UNIQUE KEY uq_notif (media_id, threshold),
            CONSTRAINT fk_notif_media FOREIGN KEY (media_id)
                REFERENCES media_queue (id) ON DELETE CASCADE
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
]

MARIADB_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_logs_ts        ON logs(ts)",
    "CREATE INDEX IF NOT EXISTS idx_media_status   ON media_queue(status)",
    "CREATE INDEX IF NOT EXISTS idx_media_delete_at ON media_queue(delete_at)",
    "CREATE INDEX IF NOT EXISTS idx_media_emby_id  ON media_queue(emby_id)",
    "CREATE INDEX IF NOT EXISTS idx_media_lib_id   ON media_queue(library_id)",
    "CREATE INDEX IF NOT EXISTS idx_ignored_emby   ON ignored_media(emby_id)",
    "CREATE INDEX IF NOT EXISTS idx_rate_limit_key ON rate_limit(`key`, ts)",
    "CREATE INDEX IF NOT EXISTS idx_notif_media    ON notifications(media_id)",
]
```

- [ ] **Step 4: Run tests**

```bash
cd /opt/claude/hygie && python -m pytest tests/test_mariadb_schema.py -v
```
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/db/schema_mariadb.py tests/test_mariadb_schema.py
git commit -m "feat(db): MariaDB DDL — all 12 tables with ENGINE=InnoDB utf8mb4"
```

---

### Task 3: Update `schema.py` `init_db()` to use DbConn + dispatch to MariaDB

**Files:**
- Modify: `backend/db/schema.py`

The goal: replace the monolithic `init_db()` that hardcodes `aiosqlite.connect()` with a dispatcher that routes to SQLite or MariaDB. The existing `init_db()` body becomes `_init_db_sqlite()`. A new `_init_db_mariadb()` runs the MariaDB DDL. Internal helpers (`_table_columns`, `_table_exists`) are updated to use `DbConn`.

- [ ] **Step 1: Replace `_table_columns` and `_table_exists` helpers**

In `backend/db/schema.py`, remove the standalone `_table_columns` and `_table_exists` functions (they're replaced by `DbConn.table_columns` and `DbConn.table_exists`). Update all callers in the file to use the DbConn methods.

Full new content for the helpers section (replace lines 255–270 of schema.py):

```python
# _table_columns and _table_exists are now methods on DbConn (backend/db/engine.py).
# Internal migration helpers call them via the db argument passed down.
```

- [ ] **Step 2: Wrap existing `init_db()` as `_init_db_sqlite()` and add dispatch**

At the bottom of `backend/db/schema.py`, after all migration helpers, replace the existing `init_db()` function with:

```python
async def _init_db_sqlite() -> None:
    """Original SQLite init — unchanged logic, uses aiosqlite directly."""
    import os as _os
    db_dir = _os.path.dirname(DB_PATH)
    if db_dir:
        _os.makedirs(db_dir, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await _migrate_logs_table(db)
        await _migrate_job_history_table(db)
        for _, create_sql, _ in _TABLES:
            await db.execute(create_sql)
        for table_name, _, expected_cols in _TABLES:
            await _ensure_columns(db, table_name, expected_cols)
        # indexes
        await db.execute("CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_media_status ON media_queue(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_media_delete_at ON media_queue(delete_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_media_emby_id ON media_queue(emby_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_media_library_id ON media_queue(library_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ignored_emby_id ON ignored_media(emby_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_rate_limit_key ON rate_limit(key, ts)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_notif_media ON notifications(media_id)")
        # defaults
        for k, v in DEFAULT_SETTINGS.items():
            await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        await db.commit()
        await _migrate_encrypt_settings(db)
        # media_servers migration, interval migration, log purge, orphan job fix
        # (keep the existing body of init_db exactly — steps 7–9 from original)
        # ... (full body of original init_db steps 7-9 here — keep as-is)


async def _init_db_mariadb() -> None:
    """MariaDB init: create tables + indexes + seed defaults."""
    from .engine import get_db
    from .schema_mariadb import MARIADB_TABLES, MARIADB_INDEXES
    async with get_db() as db:
        for table_name, ddl in MARIADB_TABLES:
            await db.execute(ddl)
        for idx_sql in MARIADB_INDEXES:
            await db.execute(idx_sql)
        for k, v in DEFAULT_SETTINGS.items():
            existing = await db.fetch_one("SELECT 1 FROM settings WHERE `key`=?", (k,))
            if not existing:
                await db.execute("INSERT INTO settings (`key`, value) VALUES (?, ?)", (k, v))
        await db.commit()
    logger.info("MariaDB schema initialized")


async def init_db() -> None:
    """Initialize database — SQLite (default) or MariaDB based on DATABASE_URL."""
    from .engine import DIALECT
    if DIALECT == "mariadb":
        await _init_db_mariadb()
    else:
        await _init_db_sqlite()
```

> **Note:** Keep the FULL body of the original `init_db()` (steps 7–9: media_servers migration, interval migration, log purge, orphan job fix) inside `_init_db_sqlite()`. Do not lose any of it.

- [ ] **Step 3: Update `_ensure_columns` and `_migrate_*` helpers**

These currently call `await _table_columns(db, table)` and `await _table_exists(db, table)` where `db` is a raw aiosqlite connection. Since `_init_db_sqlite()` still passes a raw aiosqlite connection to these helpers, they can stay using the `PRAGMA` approach — no change needed for SQLite path.

- [ ] **Step 4: Run all existing tests to verify no regression**

```bash
cd /opt/claude/hygie && python -m pytest tests/ -x -q --ignore=tests/test_db_engine.py --ignore=tests/test_mariadb_schema.py 2>&1 | tail -10
```
Expected: all tests pass (same count as before this change)

- [ ] **Step 5: Commit**

```bash
git add backend/db/schema.py
git commit -m "feat(db): init_db() dispatches to SQLite or MariaDB based on DATABASE_URL"
```

---

### Task 4: Migrate all DB modules to use `get_db()`

**Files:**
- Modify: `backend/db/repositories.py`, `backend/db/settings_store.py`, `backend/db/logs.py`,
  `backend/db/media_servers.py`, `backend/db/websocket.py`

**Pattern to apply mechanically across all files:**

Replace every:
```python
async with aiosqlite.connect(db_path) as db:
    db.row_factory = aiosqlite.Row
    async with db.execute(sql, params) as cur:
        rows = await cur.fetchall()
```
With:
```python
from .engine import get_db
async with get_db() as db:
    rows = await db.fetch_all(sql, params)
```

Replace:
```python
    async with db.execute(sql, params) as cur:
        row = await cur.fetchone()
```
With:
```python
    row = await db.fetch_one(sql, params)
```

Replace:
```python
    await db.execute(sql, params)
    await db.commit()
```
With:
```python
    await db.execute(sql, params)
    await db.commit()
```
(execute + commit stay the same — `DbConn.execute()` returns last insert id, `execute_write()` returns rowcount)

Replace:
```python
    async with db.execute(sql, params) as cur:
        async for row in cur:
            ...
```
With:
```python
    for row in await db.fetch_all(sql, params):
        ...
```

- [ ] **Step 1: Migrate `backend/db/repositories.py`**

Read the file, then apply the pattern above to every function. The `db_path` parameter that each function receives must be removed — `get_db()` reads `SQLITE_PATH` from `engine.py`. Functions that previously took `db_path: str` as a parameter now have it removed or kept for backward-compat (DB_PATH from engine is used).

> **IMPORTANT:** `get_db()` reads `backend.db.engine.SQLITE_PATH` which is set from `DB_PATH` env var. Tests patch `backend.db.engine.SQLITE_PATH` directly. No `db_path` argument needed anymore.

Show the migrated version of `save_queue_item` as an example:

```python
# Before:
async def save_queue_item(item: dict, *, db_path: str = DB_PATH) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO media_queue (...) VALUES (...)",
            (...params...)
        )
        await db.commit()

# After:
async def save_queue_item(item: dict) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO media_queue (...) VALUES (?,...)",
            (...params...)
        )
        await db.commit()
```

Apply this pattern to every function in repositories.py, settings_store.py, logs.py, media_servers.py, websocket.py.

- [ ] **Step 2: Run repository tests**

```bash
cd /opt/claude/hygie && python -m pytest tests/test_repositories.py tests/test_settings_cache.py -v
```
Expected: all PASSED

- [ ] **Step 3: Migrate the remaining backend files**

Files: `backend/auth.py`, `backend/scanner.py`, `backend/deletion.py`, `backend/notifications.py`, `backend/conditions.py`, `backend/collection.py`, `backend/discord_client.py`, `backend/arr_clients/seerr.py`

Apply the same mechanical pattern substitution.

- [ ] **Step 4: Migrate all routers**

Files: `backend/routers/stats.py`, `storage.py`, `metrics.py`, `logs.py`, `media.py`, `libraries.py`, `ignored.py`, `calendar.py`, `seerr_rules.py`, `unmonitored.py`

Same substitution. Routers that had `DB_PATH` module-level imports continue to export `DB_PATH` for test patching compatibility (keep the import, but the actual DB path comes from `engine.SQLITE_PATH`).

- [ ] **Step 5: Update test patching in `tests/test_routes.py`**

Tests currently patch `mod.DB_PATH` on all router modules. After migration, also patch `backend.db.engine.SQLITE_PATH`:

```python
import backend.db.engine as _db_engine
# In the client fixture, after setting db_path:
_db_engine.SQLITE_PATH = db_path
# In cleanup:
_db_engine.SQLITE_PATH = _orig_db
```

- [ ] **Step 6: Run full test suite**

```bash
cd /opt/claude/hygie && python -m pytest tests/ -x -q 2>&1 | tail -15
```
Expected: same pass count as before, no failures

- [ ] **Step 7: Commit**

```bash
git add backend/ tests/test_routes.py
git commit -m "refactor(db): migrate all DB access to DbConn.get_db() — removes aiosqlite.connect() calls"
```

---

### Task 5: `backend/tools/migrate_to_mariadb.py` — one-way SQLite → MariaDB migration

**Files:**
- Create: `backend/tools/__init__.py`
- Create: `backend/tools/migrate_to_mariadb.py`
- Test: `tests/test_migrate_to_mariadb.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_migrate_to_mariadb.py
"""Tests for the SQLite→MariaDB migration script (dry-run mode only — no live MariaDB)."""
import asyncio
import os
import pytest
import aiosqlite
from backend.tools.migrate_to_mariadb import read_sqlite_table, validate_sqlite_db


@pytest.fixture
def sqlite_db(tmp_path):
    db_path = str(tmp_path / "source.db")
    asyncio.get_event_loop().run_until_complete(_bootstrap_sqlite(db_path))
    return db_path


async def _bootstrap_sqlite(path: str):
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        await db.execute("INSERT INTO settings VALUES ('test_key', 'test_val')")
        await db.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, password_hash TEXT, created_at TEXT)"
        )
        await db.commit()


@pytest.mark.asyncio
async def test_read_sqlite_table(sqlite_db):
    rows = await read_sqlite_table(sqlite_db, "settings")
    assert len(rows) == 1
    assert rows[0]["key"] == "test_key"
    assert rows[0]["value"] == "test_val"


@pytest.mark.asyncio
async def test_validate_sqlite_db_ok(sqlite_db):
    tables = await validate_sqlite_db(sqlite_db)
    assert "settings" in tables
    assert "users" in tables


@pytest.mark.asyncio
async def test_validate_sqlite_db_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        await validate_sqlite_db(str(tmp_path / "nonexistent.db"))
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /opt/claude/hygie && python -m pytest tests/test_migrate_to_mariadb.py -v 2>&1 | head -10
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create migration script**

```python
# backend/tools/__init__.py
# (empty)
```

```python
# backend/tools/migrate_to_mariadb.py
"""One-way migration: SQLite → MariaDB.

Usage:
    python -m backend.tools.migrate_to_mariadb \\
        --sqlite-path /app/data/hygie.db \\
        --database-url "mysql+aiomysql://hygie:secret@localhost:3306/hygie"

The MariaDB database must already exist and be empty (schema will be created).
Migration is done table-by-table with batch inserts (1000 rows/batch).
"""
import argparse
import asyncio
import logging
import os
import sys

import aiosqlite

logger = logging.getLogger("hygie.migrate")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

BATCH_SIZE = 1000

ORDERED_TABLES = [
    "settings", "users", "libraries",
    "media_queue", "ignored_media", "seerr_user_rules",
    "logs", "job_history", "stats_history",
    "rate_limit", "expert_rules", "notifications",
]


async def validate_sqlite_db(sqlite_path: str) -> set[str]:
    """Verify the SQLite file exists and return its table names."""
    if not os.path.exists(sqlite_path):
        raise FileNotFoundError(f"SQLite database not found: {sqlite_path}")
    async with aiosqlite.connect(sqlite_path) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cur:
            rows = await cur.fetchall()
    return {r[0] for r in rows}


async def read_sqlite_table(sqlite_path: str, table: str) -> list[dict]:
    """Read all rows from a SQLite table as list of dicts."""
    async with aiosqlite.connect(sqlite_path) as db:
        db.row_factory = lambda c, r: {col[0]: r[i] for i, col in enumerate(c.description)}
        async with db.execute(f"SELECT * FROM {table}") as cur:
            return await cur.fetchall()


async def _write_mariadb_table(db_url: str, table: str, rows: list[dict]) -> None:
    """Insert rows into a MariaDB table in batches."""
    if not rows:
        logger.info("  %s: 0 rows (skipped)", table)
        return
    import aiomysql
    from backend.db.engine import _parse_mariadb_url
    kwargs = _parse_mariadb_url(db_url)
    cols = list(rows[0].keys())
    placeholders = ", ".join(["%s"] * len(cols))
    col_names = ", ".join(f"`{c}`" for c in cols)
    sql = f"INSERT IGNORE INTO `{table}` ({col_names}) VALUES ({placeholders})"
    conn = await aiomysql.connect(**kwargs, autocommit=False, charset="utf8mb4")
    try:
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            values = [tuple(r[c] for c in cols) for r in batch]
            async with conn.cursor() as cur:
                await cur.executemany(sql, values)
            await conn.commit()
        logger.info("  %s: %d rows migrated", table, len(rows))
    finally:
        conn.close()


async def migrate(sqlite_path: str, db_url: str, dry_run: bool = False) -> None:
    """Full migration pipeline."""
    logger.info("=== Hygie SQLite → MariaDB Migration ===")
    logger.info("Source: %s", sqlite_path)
    logger.info("Target: %s", db_url.split("@")[-1])  # hide credentials

    present_tables = await validate_sqlite_db(sqlite_path)
    logger.info("Tables in SQLite: %s", sorted(present_tables))

    # Initialize MariaDB schema
    if not dry_run:
        os.environ["DATABASE_URL"] = db_url
        import importlib
        import backend.db.engine as eng
        importlib.reload(eng)
        await eng.init_db_pool()
        from backend.db.schema import _init_db_mariadb
        await _init_db_mariadb()
        logger.info("MariaDB schema created")

    for table in ORDERED_TABLES:
        if table not in present_tables:
            logger.warning("  %s: not in SQLite (skipped)", table)
            continue
        rows = await read_sqlite_table(sqlite_path, table)
        if dry_run:
            logger.info("  %s: %d rows (DRY RUN)", table, len(rows))
        else:
            await _write_mariadb_table(db_url, table, rows)

    logger.info("=== Migration complete ===")
    if dry_run:
        logger.info("DRY RUN — no data written to MariaDB")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate Hygie from SQLite to MariaDB")
    parser.add_argument("--sqlite-path", required=True, help="Path to hygie.db")
    parser.add_argument("--database-url", required=True, help="MariaDB connection URL")
    parser.add_argument("--dry-run", action="store_true", help="Read SQLite but don't write")
    args = parser.parse_args()
    asyncio.run(migrate(args.sqlite_path, args.database_url, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

```bash
cd /opt/claude/hygie && python -m pytest tests/test_migrate_to_mariadb.py -v
```
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/tools/ tests/test_migrate_to_mariadb.py
git commit -m "feat(tools): add migrate_to_mariadb CLI script for one-way SQLite→MariaDB migration"
```

---

### Task 6: Docker Compose MariaDB profile + lifespan + README

**Files:**
- Modify: `docker-compose.yml`
- Modify: `backend/main.py` (lifespan: call `init_db_pool` / `close_db_pool`)
- Modify: `README.md`

- [ ] **Step 1: Add MariaDB service to `docker-compose.yml`**

Add after the existing `hygie` service:

```yaml
  mariadb:
    image: mariadb:11
    profiles: [mariadb]
    restart: unless-stopped
    environment:
      MYSQL_DATABASE: hygie
      MYSQL_USER: hygie
      MYSQL_PASSWORD: ${DB_MARIADB_PASSWORD:-hygie_secret}
      MYSQL_ROOT_PASSWORD: ${DB_MARIADB_ROOT_PASSWORD:-root_secret}
    volumes:
      - hygie_mariadb:/var/lib/mysql
    healthcheck:
      test: ["CMD", "mariadb-admin", "ping", "-h", "localhost", "-u", "hygie", "-phygie_secret"]
      interval: 5s
      timeout: 5s
      retries: 10

volumes:
  hygie_mariadb:
```

Add to the `hygie` service's `environment` block:
```yaml
      DATABASE_URL: ${DATABASE_URL:-}
```

Add `depends_on` condition for mariadb profile to the hygie service:
```yaml
    depends_on:
      mariadb:
        condition: service_healthy
        required: false  # only enforced when profile is active
```

- [ ] **Step 2: Update `backend/main.py` lifespan**

Find the `lifespan` async context manager and add pool init/close:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    from .db.engine import init_db_pool, close_db_pool
    await init_db()        # existing call — now dispatches to sqlite or mariadb
    await init_db_pool()   # NEW: creates aiomysql pool if DATABASE_URL set (no-op for sqlite)
    # ... existing startup code (scheduler, etc.) ...
    yield
    # ... existing shutdown code ...
    await close_db_pool()  # NEW
```

- [ ] **Step 3: Add `aiomysql` to requirements.txt**

```
aiomysql==0.2.0
```

- [ ] **Step 4: Add README section**

In `README.md`, add a "Database" section with:

```markdown
## Database

Hygie supports **SQLite** (default) and **MariaDB**.

| Setup | Recommended for |
|-------|----------------|
| SQLite (default) | Personal use, < 200k media items, zero config |
| MariaDB | > 200k items, or you already run a MySQL/MariaDB server |

### SQLite (default)

No configuration needed. Data is stored in `/app/data/hygie.db`.

### MariaDB

**Option A — Embedded MariaDB (Docker Compose):**
```bash
# First-time setup
DB_MARIADB_PASSWORD=your_password docker compose --profile mariadb up -d

# Hygie connects automatically when DATABASE_URL is set:
DATABASE_URL=mysql+aiomysql://hygie:your_password@mariadb:3306/hygie
```

**Option B — External MariaDB server:**
Set `DATABASE_URL` in your Docker run command or compose file:
```
DATABASE_URL=mysql+aiomysql://user:pass@your-mariadb-host:3306/hygie
```

### Migrating from SQLite to MariaDB

```bash
# Stop Hygie, then run the migration script:
docker exec hygie python -m backend.tools.migrate_to_mariadb \
  --sqlite-path /app/data/hygie.db \
  --database-url "mysql+aiomysql://hygie:pass@mariadb:3306/hygie"

# Verify with dry-run first:
docker exec hygie python -m backend.tools.migrate_to_mariadb \
  --sqlite-path /app/data/hygie.db \
  --database-url "mysql+aiomysql://hygie:pass@mariadb:3306/hygie" \
  --dry-run
```
```

- [ ] **Step 5: Run full test suite one last time**

```bash
cd /opt/claude/hygie && python -m pytest tests/ -q 2>&1 | tail -5
```
Expected: all passing

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml backend/main.py requirements.txt README.md
git commit -m "feat(db): MariaDB Docker profile, lifespan pool init, README database guidance"
```

---

### Task 7: Version bump + tag v3.0.0-alpha.1

**Files:**
- Modify: `backend/version.py`

- [ ] **Step 1: Bump version**

```python
# backend/version.py
VERSION = "3.0.0-alpha.1"
```

- [ ] **Step 2: Run full suite + build Docker**

```bash
cd /opt/claude/hygie && python -m pytest tests/ -q 2>&1 | tail -5
docker build -t hygie:3.0.0-alpha.1 .
```
Expected: tests pass, Docker build succeeds

- [ ] **Step 3: Tag and push**

```bash
git add backend/version.py
git commit -m "chore: bump version to 3.0.0-alpha.1 (DB abstraction phase)"
git tag v3.0.0-alpha.1
git push origin main --tags
```
