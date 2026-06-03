# Changelog

All notable changes to Hygie are documented here.

---

## [3.4.1] — 2026-06-03

Fixes for issues identified during the Grok audit (senior engineer + senior architect review).

### Fixed

- **Init order for DB pool (MariaDB)** — `init_db_pool()` would crash with a raw traceback if `DATABASE_URL` was malformed or the server unreachable, *before* the `StartupValidator` could produce a structured CRITICAL message. Errors are now caught and properly injected into the validator.
- **Fragile MariaDB URL parsing** — Naive `split("@")` parsing broke on IPv6 addresses, percent-encoded special characters in passwords (e.g. `p%40ss`), and non-standard ports. Replaced with `urllib.parse.urlparse` + `unquote`.
- **Circuit breakers not wired for Emby + Plex** — The circuit breaker existed in `arr_clients/circuit_breaker.py` and was used for Radarr/Sonarr, but `emby_client.py` and `plex_client.py` did not use it. If Emby/Plex was down, every item would attempt a connection up to the 2h job timeout.
  - `get_users()` and `get_items_in_library()`: breaker per `server_id` (threshold 5 failures, recovery 120s)
  - `PlexClient._get()`: all Plex requests now go through the breaker
- **Pipeline step failure visibility** — `SizeLookupStep` and `TorrentHashStep` logged their failures at `DEBUG` level (invisible in production). Promoted to `INFO` + recorded in `ctx.step_warnings`. The pipeline now logs a consolidated `WARNING` at the end of a deletion run if any soft-failed steps occurred.

### Changed

- `StartupValidator` now accepts a `db_pool_init_error` parameter to cleanly relay MariaDB pool initialization errors.

### Tests

- +4 tests for `_parse_mariadb_url` (special characters, multiple schemes, default port handling)
- +2 tests for `StartupValidator` (pool error injection, normal connectivity check)
- Total: **352 passed** (was 346 in v3.4.0)

---

## [3.4.0] — 2026-06-03

Release focused on security and performance audit fixes.

### Security (CRITICAL)

- **SSRF** — `POST /api/settings/sync-arr-from-seerr` was missing URL scheme validation (the `test-arr` SSRF fix from v3.3.0 was not applied to this endpoint).
- **Timing attack on login** — Non-existent usernames responded ~170ms faster (no dummy hash was being computed/verified). A dummy hash is now always verified.
- **Rate limit bypass** — `rate_limit` was called *after* password verification, allowing an attacker to alternate success/failure to bypass the block. It is now called *before*.
- **Missing rate limit on refresh** — The `/api/auth/refresh` endpoint had no rate limiting.
- **Non timing-safe comparison** — Public dashboard password check used `!=` instead of `hmac.compare_digest`.

### Security (P0)

- **WORKERS > 1** — Startup with multiple workers was only logging a warning but still started the app. Now calls `sys.exit(1)` if `WORKERS > 1` (asyncio locks do not cross OS process boundaries).
- **Default MariaDB passwords** — `hygie_secret` / `root_secret` removed from docker-compose fallbacks. `DB_MARIADB_PASSWORD` is now strictly required with a clear error message.

### Fixed

- **CORS wildcard** — Fallback `["*"]` changed to explicit `["http://localhost:8000", "http://localhost:5173"]`.
- **Double-scheduled cleanup** — `_internal_cleanup` was scheduled twice (12h interval + 3am cron). Now only the 3am cron remains.
- **Fire-and-forget storage task** — `asyncio.create_task()` without error callback; exceptions are now logged via `add_done_callback`.
- **`library_id` sort** — `library_id` was missing from `_SORT_MAP`, causing silent fallback to `delete_at`. Fixed.

### Performance

- **N×M → N+M HTTP calls in `reevaluate_library_queue`** — Per-item `get_user_data(uid, emby_id)` × per-user replaced by a single batch `get_library_user_data` before the loop.
- **N+1 DB queries for expert rules** — `get_expert_rules()` was called per evaluated item. Now loaded once per scan and passed via cache.
- **Discord rate limiting** — `send_alert` now has a minimum 220ms spacing between calls to avoid webhook blacklisting during batch deletions.

### CI / Quality

- **Coverage measurement** — `pytest --cov=backend --cov-fail-under=50` added to CI (current: ~50%).
- **Strict frontend lint** — Removed `continue-on-error: true` from the ESLint step.
- **pytest-cov** added to `requirements-dev.txt`.

---

## [3.3.0] — 2026-06-03

### Security

- **SSRF fix** — `POST /api/settings/test-arr` now validates the URL scheme (http/https only) using the same guard as all other server URL endpoints. `file://`, `ftp://`, and similar schemes are rejected with HTTP 422.

### Fixed

- **Race condition in manual delete** — In `POST /media/{id}/delete-now`, the row is now re-verified within the same DB context before the final `UPDATE status='deleted'`, preventing double-deletes on overlapping requests.
- **Status enum validation** — `GET /api/media` now rejects unknown `status` query values with 422 instead of running an unconstrained SQL query.
- **`nonlocal` antipattern** in `deletion.py` — Replaced `nonlocal _error_count` with an accumulator dict. Semantics unchanged; cleaner closure behavior.

### Changed

- `POST /api/settings/media-servers` now returns HTTP 201 Created.
- `stores/status.js start()` is now idempotent (calling it twice no longer stacks duplicate polling intervals).
- `ServersTab.vue` clears the `_detectTimers` map on `onUnmounted` (fixes memory leak on component remount).
- `verify_password()` in `auth.py` now logs unexpected errors at DEBUG instead of silently swallowing them.
- `db/engine.py _q()` now documents the known limitation with `?` inside string literals.
- New `backend/constants.py` module centralizing server types (`SERVER_EMBY`, `SERVER_JELLYFIN`, `SERVER_PLEX`) and media types.

### Refactored

- `_evaluate_item` in `legacy_conditions.py`: extracted `_aggregate_user_data()` and `_resolve_arr_ids()` helpers. The function is now ~80 lines shorter and individually testable.
- `_run_scan_body` in `_orchestrator.py`: extracted `_scan_single_server()` helper, reducing nesting depth.

### Tests

- +7 tests for the new `_aggregate_user_data` and `_resolve_arr_ids` helpers.
- +3 SSRF tests for the `test-arr` endpoint.
- Total: **344 passed** (was 334 in v3.2.0).

---

## [3.2.0] — 2026-06-02

This is the final v3.0.0-series stabilization release. It supersedes all prior v3 alphas and includes the complete frontend rewrite (Vue 3), MariaDB support, expert rules engine, full Plex integration, public calendar, 8-language i18n, and numerous architectural improvements.

(See the detailed "Added / Changed / Fixed" sections in the v3.0.0 entry below for the full scope. v3.2.0 focused on bug fixes, test repair, and polish after the major v3 refactor.)

---

## [3.1.x series] (summarized)

Multiple patch releases (3.1.1 through 3.1.10) addressed:

- last_played / view_count accuracy (Emby activity log retrieval and refresh on scan)
- UI improvements (sidebar collapse persistence, server status, queue indicators, scan animations, logo color reflecting connected servers)
- Icon and redirect fixes (Font Awesome 6 renames, public calendar loops)
- Rule regressions, CI lint, and release process fixes
- Public calendar admin password bypass and redirect issues
- Docker compose tag handling for auto-update scenarios
- Numerous i18n, Discord notification deduplication, and frontend refactors (removal of defineComponent/h() patterns in favor of standard templates)

Full per-version details were previously listed in release bodies and consolidated here.

---

## [3.0.0] — 2026-06-02

Major release: complete Vue 3 frontend rewrite, MariaDB support, expert rules, Plex integration, public calendar, and 8-language internationalization.

### Added (highlights)

- **Frontend**: Vue 3 + Vite + Pinia + vue-i18n (replacing vanilla JS/Jinja2). 8 languages supported.
- **Expert Rules**: Full visual builder with AND/OR condition groups, multi-library targeting, per-rule "Run" button.
- **Database**: Full MariaDB support via `DATABASE_URL`, bidirectional migration tools + UI, `DbConn` abstraction.
- **Plex**: Complete client, webhook support, expert rules integration, poster overlays.
- **Public Calendar**: Shareable upcoming deletions view (`/<slug>`), optional password, multi-language, deep links back to the correct media server.
- **i18n**: Backend log messages via `lm()` + JSON locale files (8 languages); guard scripts for completeness.
- **Architecture**: Extracted routers for scheduler, public endpoints, database; APScheduler singleton; constants module.
- Many new tests, CI improvements, and operational features (embedded MariaDB profile, healthcheck parity, etc.).

### Changed / Fixed

(See the raw upstream CHANGELOG for the exhaustive list of refactors, bug fixes for scans, notifications, i18n escaping, double-scheduling, SSRF hardening, etc.)

---

## [2.8.0] — 2026-05-29

### Added
- Pydantic models for expert rules (`ConditionField`, `ConditionOp`, etc.).
- Rule evaluation engine (`backend/rules/engine.py`).
- `expert_rules` table + CRUD.
- Integration of expert rules into the scanner.
- Notifications deduplication table.
- Per-library stats.

---

## [2.7.0] — 2026-05-28

### Added
- Repository pattern.
- Async generators for paginated Seerr data.
- Custom exception hierarchy.

---

## [2.6.0] — 2026-05-28

### Added
- Rate limiting (SQLite-backed).
- Warnings when encryption key is missing.
- API key masking in responses.
- Global stats router.
- Migration of interval settings from hours to minutes.

---

## Older Releases (v2.4.0 and prior)

### v2.4.0 — Stability, Security & Performance

**Stability**
- Parallel deletions using `Semaphore(3)` — faster without overloading external services.
- HTTP retry with exponential backoff (×3: 1s/2s/4s) on `TimeoutException` and `ConnectError`.
- 5s timeout on every WebSocket send — a slow client no longer blocks others.
- Robust `init_db()` supporting in-memory databases (`:memory:`).

**Performance**
- Three new DB indexes: `media_queue(emby_id)`, `media_queue(library_id)`, `ignored_media(emby_id)`.
- `notified_detected` and `notified_thresholds` columns promoted into the main schema (no more lazy migrations).

**Security**
- Rate limiter: dead code removed, periodic cleanup every 500 calls (bounded memory).
- `sanitize_url()`: `api_key`, `token`, `password` parameters are now masked in logs.
- Scheduler intervals clamped to `[1, 10080]` — zero or negative values no longer crash APScheduler.
- Unified password validation (frontend + backend): minimum **8 characters**.

**Code Quality**
- Shared `_path_matches()` utility for Radarr/Sonarr path matching (removes duplication).
- Enriched Discord logs: media titles are logged on webhook failure.
- Health check `/health` now detects scheduler jobs without `next_run_time` (zombie jobs).
- `http_retry()` exported from the database module for reuse by all clients.

**Discord Notifications (v2.3.3, integrated here)**
- Immediate notification on media detection (with scheduled deletion date shown).
- Configurable thresholds in the Discord settings tab (e.g. `7,1` for 7 days and 1 day before).
- Dynamic titles for any threshold (e.g. `📅 Deletion in 14 days`).
- Color-coded queue: Yellow `< 30d`, Orange `< 14d`, Red `< 7d`, animated glowing red `< 3d`.

**Tests**
- 15 new tests (`test_v240_fixes.py`): URL sanitization, HTTP retry, rate limiter, DB indexes.
- Total: 169 tests.

---

## [2.3.2]

- fix: Use calendar-date diff in UI countdown to match overlay logic (prevents 1-day overshoot).

---

## [2.3.x and earlier]

See git tags and historical release bodies for incremental fixes (Jellyfin support in v2.1, encryption hardening in v2.0, various proxy/QUI/qBittorrent integration fixes in the v1.2 series, etc.).

For the complete historical list of small patches, refer to the git tag messages or the GitHub Releases page.

---

**Note**: This CHANGELOG has been normalized to English. Original French notes from recent releases (particularly the Grok audit series in v3.4.x) have been translated for consistency while preserving technical accuracy. For the absolute latest or raw upstream notes, see https://github.com/carryozor/hygie/releases and the raw CHANGELOG.md.