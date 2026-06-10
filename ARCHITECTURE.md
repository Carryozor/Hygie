# Hygie — Architecture Decisions

This document records the key architectural constraints, decisions, and
technical debt of Hygie v3.x. It is updated at each minor release.

---

## Constraints you MUST respect

### Single-worker deployment (CRITICAL)

Hygie uses `asyncio.Lock()` objects in `_job_state.py` for scan and deletion
job exclusivity. These locks exist only within a single Python process.

**If you run Gunicorn with `--workers 2` or more:**
- Two scans can run simultaneously
- Both will write to `media_queue` concurrently → duplicate entries
- Both will call Emby/Radarr deletion APIs for the same item → data corruption

**Required configuration:**
```
WORKERS=1
```

This is enforced at startup: the `StartupValidator` logs a CRITICAL error if
`WORKERS > 1`.

**To scale horizontally:** Deploy separate Hygie instances with separate
databases, one per media server. Do not share a DB between instances.

---

## Design decisions

### SQLite and MariaDB (v3.0)

Both dialects are fully supported and will remain so. The `DbConn` abstraction
in `db/engine.py` translates `?` placeholders to `%s` for MariaDB and provides
a uniform API. MariaDB is recommended for multi-user deployments or when backup
via `mysqldump` is preferred.

The `migrations.py` runner is dialect-aware (m001–m008). All new migrations
must use `db.table_columns()` instead of `PRAGMA table_info()`.

### In-process APScheduler (v3.0)

The scan and deletion jobs run inside the same process as the FastAPI API via
APScheduler. This is intentional for simplicity in single-instance deployments.

**Limitation:** A long scan can degrade API response times if the event loop is
saturated. Mitigation: scans use `asyncio.Semaphore` for parallel library
scanning, and both jobs have hard timeouts (scan: 2h, deletion: 1h).

**Future:** Separating scanner and API into distinct processes would require an
inter-process job queue (Redis, RabbitMQ) and distributed lock management. This
is a v4.0 consideration, not currently planned.

### Unified rule evaluation engine (v3.6)

`rules/engine.py` is the single comparison engine for both library formats:

- Expert rules are evaluated directly (Emby/Jellyfin and Plex scanners).
- Per-library legacy conditions are converted on the fly into an `ExpertRule`
  by the adapter in `rules/legacy_conditions.py` (`_legacy_condition_to_group`)
  and evaluated by the same engine. Legacy edge-case semantics (never-watched
  items always satisfy `days_not_watched`, unknown fields evaluate to False)
  are preserved and pinned by tests in `tests/test_conditions.py`.

`never_watched` is now a first-class `ConditionField` usable in expert rules.

### DeletionPipeline (v3.1)

`deletion_pipeline.py` implements the deletion workflow as ordered steps:
`SizeLookupStep` → `TorrentHashStep` → `DiscordNotifyStep` → `ServerResolveStep`
→ `MediaServerStep` → `ArrStep` → `SeerrStep` → `QbitStep` → `StatsStep`.

**Adding a new integration:** Create a new `DeletionStep` subclass and insert
it in `build_default_pipeline()` at the correct position. Steps that must run
before `MediaServerStep` (so the item still exists on the server) are:
`SizeLookupStep`, `TorrentHashStep`, `DiscordNotifyStep`.

### Circuit breakers (v3.1, fully wired in v3.6)

`arr_clients/circuit_breaker.py` provides a simple three-state circuit breaker
(`CLOSED → OPEN → HALF_OPEN`). Register a breaker with `get_breaker("service_name")`.
The breaker state is exposed at `/health` in the `circuit_breakers` field.

Wiring: Emby (`emby:{server_id}`) and Plex (`plex:{server_id}`) clients route
scan-critical calls through their breakers directly; Radarr/Sonarr go through
`with_retry(..., service="radarr"/"sonarr")`; Seerr's cache builder goes
through the `seerr` breaker and converts `CircuitOpenError` to
`ArrClientError` so callers degrade gracefully.

### Job correlation via ContextVar (v3.1)

`db/logs.py` uses `contextvars.ContextVar` to propagate the current job ID to
all `add_log()` calls within a job's async context. Call `set_job_context(run_id)`
at the start of a job, reset the token in `finally`. All logs from that job
will carry `job_id = run_id`, making them filterable from `job_history`.

### Global in-process caches

Several module-level variables cache data to avoid DB/HTTP calls on every
request. They are all incompatible with multi-worker deployments.

| Module | Variable | TTL |
|--------|----------|-----|
| `settings_store.py` | `_settings_cache` | 30s |
| `media_servers.py` | `_ms_cache` | 30s |
| `collection.py` | `_overlay_cache` | session |
| `proxy.py` | `_proxy_whitelist` | 5min |
| `qbit_client.py` | `_sid_cookie` | session |

If multi-worker ever becomes a requirement, these caches must be moved to Redis.

### API versioning

All current endpoints are at `/api/<resource>`. These constitute the implicit
v1 API. A `/api/v2/` prefix will be introduced for any breaking changes.
Current clients (the bundled Vue frontend) use `/api/` without a version
and will not be affected.

---

## Technical debt backlog

| Item | Priority | Notes |
|------|----------|-------|
| Separate scanner/deletion workers | v4.0 | Requires job queue + distributed locks |
| DB-level distributed locks | v4.0 | `SELECT GET_LOCK()` (MariaDB) / `advisory_lock` (SQLite) |
| Normalize `media_queue` schema | v4.0 | Server-specific columns belong in extension tables |
| Retire `frontend/static/` | v3.2 | Only used if Vue build fails; audit templates first |
