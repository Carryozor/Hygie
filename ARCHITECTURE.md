# Hygie — Architecture Decisions

This document records the key architectural constraints, decisions, and
technical debt of Hygie. It is updated at each minor release.

---

## Deployment modes

### Single-worker (default, SQLite or MariaDB)

```
WORKERS=1                    # default
HYGIE_LOCK_BACKEND=asyncio   # default, can be omitted
```

Scan and deletion jobs are serialized by an `asyncio.Lock` (`AsyncioLockBackend`).
Safe for SQLite and MariaDB. No additional configuration required.

### Multi-worker (MariaDB required)

```
WORKERS=4
DATABASE_URL=mysql+aiomysql://hygie:secret@host:3306/hygie
HYGIE_LOCK_BACKEND=mariadb
```

`MariaDBAdvisoryLockBackend` (`GET_LOCK()`/`RELEASE_LOCK()`) serializes
scan and deletion across OS processes. Each worker runs its own APScheduler
instance; only the first worker to acquire the advisory lock runs the job —
the others return immediately (non-blocking `GET_LOCK(name, 0)`). If the lock
cannot be acquired, `LockNotAvailable` is raised and silenced at the call site.

WebSocket log streaming uses DB polling (`SELECT … WHERE id > cursor`) so
clients connected to any worker receive logs written by any worker.

In-process caches (`_settings_cache`, `_ms_cache`) have 30-second TTLs —
a settings write propagates to all workers within 30 s.

**Startup validation:** `StartupValidator._check_worker_count()` enforces:
- `WORKERS > 1` + SQLite → CRITICAL (SQLite unsafe for concurrent writers)
- `WORKERS > 1` + `HYGIE_LOCK_BACKEND != mariadb` → CRITICAL

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

**Multi-worker:** Each worker runs its own APScheduler. All workers fire the
job at approximately the same time (they share the same `job_history` table and
compute identical `next_run_time` from it). The advisory lock ensures only one
worker proceeds; the others skip the cycle silently via `LockNotAvailable`.

**Limitation:** A long scan can degrade API response times if the event loop is
saturated. Mitigation: scans use `asyncio.Semaphore` for parallel library
scanning, and both jobs have hard timeouts (scan: 2h, deletion: 1h).

### LockBackend abstraction (v3.7) + non-blocking acquire (v4.0)

`_lock_backend.py` provides a `LockBackend` Protocol with two implementations:

- `AsyncioLockBackend` — wraps `asyncio.Lock()`, in-process only (default)
- `MariaDBAdvisoryLockBackend` — uses `GET_LOCK(name, 0)` (non-blocking) and
  `RELEASE_LOCK()`. If the lock is not available, raises `LockNotAvailable`.
  `run_scan()`, `run_scan_libraries()`, and `_run_deletion_guarded()` catch
  `LockNotAvailable` and return silently.

`_job_state.py` imports `scan_lock` and `deletion_lock` singletons from
`_lock_backend.py`. All callers use them as async context managers (`async with`)
without knowing the backend. Switch by setting `HYGIE_LOCK_BACKEND=mariadb`.

### MediaServerFactory (v3.7)

`media_server_factory.py` centralizes Emby/Plex dispatch with two helpers:

- `get_server_item_id(server, item)` — returns `plex_rating_key` or `emby_id`
- `delete_server_item(server, item, *, server_id=None)` — dispatches to
  `PleXClient.delete_item()` or `emby_client.delete_item()`

Both `deletion.py` and `deletion_pipeline.py` use these helpers, eliminating
scattered `is_plex()` checks at the call sites.

### QueueEntry TypedDict (v3.7)

`types.py` defines `QueueEntry(TypedDict, total=False)` with 22 fields.
Required fields (`emby_id`, `title`, `media_type`, `library_id`, `library_name`,
`file_path`, `detected_at`, `delete_at`) are marked with `Required[T]`.
All queue-building code (`_queue_entry.py`, `legacy_conditions.py`) annotates
return types with `QueueEntry`.

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
request. Some have `asyncio.Lock` guards (v3.7) to prevent cache stampedes
under concurrent async requests — see the Lock column below; `_ms_cache`,
`_overlay_cache`, `_proxy_whitelist`, and `_sid_cookie` do not, so concurrent
requests after TTL expiry can issue duplicate refreshes (the refreshed value
is cheap/idempotent in each case, so this is a minor inefficiency, not a
correctness bug).

In multi-worker mode, each worker has its own copy of these caches. A write in
worker A does not immediately propagate to workers B/C/D. Workers B/C/D will
read stale data until their TTL expires. For the default 30 s TTL, the maximum
staleness is 30 s — acceptable for settings and media server configuration.

| Module | Variable | TTL | Lock |
|--------|----------|-----|------|
| `settings_store.py` | `_settings_cache` | 30s | `_settings_cache_lock` |
| `discord_client.py` | `_discord_alert_last_ts` | 60s | `_discord_alert_lock` |
| `media_servers.py` | `_ms_cache` | 30s | — |
| `collection.py` | `_overlay_cache` | session | — |
| `proxy.py` | `_proxy_whitelist` | 5min | — |
| `qbit_client.py` | `_sid_cookie` | session | — |

### API versioning

All current endpoints are at `/api/<resource>`. These constitute the implicit
v1 API. A `/api/v2/` prefix will be introduced for any breaking changes.
Current clients (the bundled Vue frontend) use `/api/` without a version
and will not be affected.

---

## Technical debt backlog

| Item | Priority | Notes |
|------|----------|-------|
| Normalize `media_queue` schema | v4.x | Server-specific columns belong in extension tables |
| Retire `frontend/static/` | v4.x | Only used if Vue build fails; audit templates first |
| Move in-process caches to shared store | v5.0 | Eliminates 30s staleness in multi-worker mode |
