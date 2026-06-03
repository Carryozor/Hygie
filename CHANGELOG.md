# Changelog

All notable changes to Hygie are documented here.

---

## [3.4.0] — 2026-06-03

### Security (CRITIQUE)
- **SSRF** — `POST /api/settings/sync-arr-from-seerr` validait l'URL (fix SSRF sur test-arr en v3.3.0 mais oubli sur cet endpoint)
- **Timing attack** — login ne comparait plus le hash si le username était inexistant (réponse 170ms plus rapide → enumération). Le hash dummy est maintenant toujours vérifié
- **Rate limit bypass** — le rate_limit était appelé APRÈS la vérification du mot de passe; un attaquant pouvant alterner succès/échec bypasse le blocage. Désormais appelé AVANT
- **Rate limit refresh** — endpoint `/api/auth/refresh` n'avait aucun rate limiting
- **Comparaison timing-safe** — `password != cfg_pwd` sur le dashboard public remplacé par `hmac.compare_digest`

### Security (P0)
- **WORKERS > 1** — le démarrage avec plusieurs workers ne bloquait pas (WARN loggé mais app démarrait quand même). Désormais `sys.exit(1)` si WORKERS > 1 détecté — les locks asyncio ne traversent pas les barrières OS
- **Passwords MariaDB par défaut** — `hygie_secret` / `root_secret` supprimés du docker-compose comme valeurs de fallback; `DB_MARIADB_PASSWORD` est maintenant requis avec message d'erreur explicite

### Fixed
- **CORS wildcard** — fallback de `["*"]` changé en `["http://localhost:8000", "http://localhost:5173"]`
- **Double schedule cleanup** — `_internal_cleanup` était schedulé deux fois (interval 12h + cron 3h). Désormais un seul cron à 3h
- **Storage task fire-and-forget** — `asyncio.create_task()` sans callback; les exceptions silencieuses maintenant loggées via `add_done_callback`
- **Sort library_id** — `library_id` absent de `_SORT_MAP` → fallback silencieux sur `delete_at`. Corrigé

### Performance
- **N+1 HTTP dans `reevaluate_library_queue`** — l'appel `get_user_data(uid, emby_id)` par item × par user remplacé par un batch `get_library_user_data` avant la boucle (N×M → N+M)
- **N+1 DB expert rules** — `get_expert_rules()` appelé par item évalué. Désormais chargé une fois par scan et passé en cache (`rules_cache=`)
- **Discord rate limiting** — `send_alert` dispose d'un rate limiter à 220ms minimum entre appels pour éviter le blacklisting du webhook lors de suppressions en batch

### CI/CD
- **Coverage mesuré** — `pytest --cov=backend --cov-fail-under=50` ajouté au CI (actuel: 50%)
- **Frontend lint strict** — `continue-on-error: true` supprimé du step lint ESLint
- **`pytest-cov`** ajouté aux requirements-dev.txt

---

## [3.3.0] — 2026-06-03

### Security
- **SSRF fix** — `POST /api/settings/test-arr` now validates the URL scheme (http/https only) using the same `_validate_server_url` guard as all other server endpoints; `file://`, `ftp://`, and similar schemes are rejected with HTTP 422

### Fixed
- **Race condition** in manual delete (`POST /media/{id}/delete-now`) — row is re-verified within the same DB context before the final `UPDATE status='deleted'`, preventing double-delete when two requests overlap
- **Status enum validation** — `GET /api/media` now rejects unknown `status` values with 422 instead of executing an unconstrained SQL query
- **nonlocal antipattern** in `deletion.py` — replaced `nonlocal _error_count` with an accumulator dict `_counters`; semantics unchanged, no reliance on CPython closure-capture edge cases

### Changed
- **HTTP semantics** — `POST /api/settings/media-servers` returns `201 Created` instead of 200
- **Frontend `start()` guard** (`stores/status.js`) — calling `start()` twice no longer stacks duplicate `setInterval` handles; idempotent guard added
- **Memory leak** in `ServersTab.vue` — `_detectTimers` map is now cleared in `onUnmounted`, preventing timer accumulation across component remounts
- **`verify_password`** in `auth.py` — broad `except Exception` now logs at DEBUG level instead of silently swallowing errors
- **SQL placeholder doc** in `db/engine.py` — `_q()` now documents the known limitation with `?` inside string literals
- **`backend/constants.py`** — new module centralizing server type strings and media type strings

### Refactored
- **`_evaluate_item`** decomposed: extracted `_aggregate_user_data()` and `_resolve_arr_ids()` helpers — function is now ~80 lines shorter
- **`_run_scan_body`** — server iteration logic extracted into `_scan_single_server()` helper, reducing nesting depth

### Tests
- +7 tests for `_aggregate_user_data` and `_resolve_arr_ids` in `test_conditions.py`
- +3 SSRF tests for `test-arr` endpoint in `test_routes.py`
- Total: **344 passed** (was 334 in v3.2.0)

---

## [3.0.0] — 2026-06-02

This is the final v3.0.0 release. It supersedes all v3.0.0-alpha builds and incorporates
everything developed since v2.8.0, including a complete frontend rewrite, MariaDB support,
an expert rules multi-library engine, full Plex integration, public calendar, 8-language
i18n, and many architectural improvements.

### Added

#### Frontend — Complete Vue 3 rewrite
- **Vue 3 + Vite + Pinia + vue-i18n** — replaces the legacy vanilla JS/Jinja2 frontend
- **8 languages** — French, English, German, Spanish, Italian, Portuguese, Dutch, Polish
- **Sidebar** — collapsible server/library tree, per-server type colors (Emby=green, Jellyfin=purple, Plex=orange), real-time scan/deletion progress bars with animated dots
- **Dashboard** — global stats, per-server status arcs on the logo (color = server type)
- **Queue view** — filterable, sortable, with media type badges and poster images
- **Calendar view** — upcoming deletions by month with day detail panel
- **Rules view** — expert rule builder with condition groups (AND/OR), drag handles, logic recap; simple Seerr per-user rules
- **Settings** — tabbed UI with per-service icons, test buttons, server type auto-detection
- **Logs view** — real-time WebSocket stream with level filters and log retention controls
- **Ignored view** — ignored media management with optional expiry
- **Library view** — per-library queue and stats
- **Setup & login** — first-run setup wizard, JWT login with refresh token support
- **Public dashboard** — shareable calendar at `/<slug>` (no `/public/` prefix), optional password protection, language selector (8 languages, stored in localStorage), server/library grouping, "View on Server" link with correct `serverId` for Emby/Jellyfin

#### i18n & Log translations
- **vue-i18n v9** for all UI strings across 8 languages
- **`backend/locales/*.json`** — backend log messages translated in 8 languages via `lm()` helper
- **`backend/logmsg.py`** — thin loader reading from JSON locale files (no more hardcoded Python dicts)
- **Server connection error codes** — `dns_failure`, `connection_refused`, `timeout`, `http_401`, etc. translated via `settings.servers.errors.*` locale keys
- **`scripts/check_lm_imports.py`** — guard script to catch missing `lm()` imports before CI

#### Database
- **MariaDB support** — `DATABASE_URL` env var switches from SQLite to MariaDB/MySQL
- **`DbConn` abstraction** (`backend/db/engine.py`) — unified async API for SQLite and MariaDB with dialect-aware queries, connection pool, `table_exists()`, `table_columns()`
- **Bidirectional migration** — `backend/tools/migrate_to_mariadb.py` (SQLite → MariaDB) and `backend/tools/migrate_to_sqlite.py` (MariaDB → SQLite); both accessible from the Settings → Database UI
- **Database settings tab** — shows dialect, connection info, per-table row counts; test connection; start migration with progress polling; restart instructions banner
- **`routers/database.py`** — `GET /api/database/info`, `POST /api/database/test`, `POST /api/database/migrate`, `GET /api/database/migrate/status`
- **Embedded MariaDB** (`EMBEDDED_MARIADB=true`) — single-container all-in-one mode with `docker/entrypoint.sh` that initializes and starts mysqld before uvicorn; `docker-compose.embedded-mariadb.yml` override
- **`refresh_tokens` table** added to MariaDB schema (`schema_mariadb.py`) and migration order
- **`backend/db/settings_store.py`** — `get_language_sync()` public function for cache reads without I/O

#### Scheduler & Architecture
- **`backend/_scheduler_instance.py`** — APScheduler singleton extracted from `main.py`; breaks the circular import `main → routers/settings → main.reschedule_jobs`
- **`routers/scheduler.py`** — new router: `/api/version`, `/api/scheduler/status`, `/api/scheduler/run/{job_id}`, `/api/scan/trigger`, `/api/deletion/trigger`, `/api/scan/library/{library_id}`, `/api/emby-collection/sync`, `/api/jobs/history`, `/api/media/job-status`
- **`routers/public.py`** — public calendar endpoint extracted from `main.py`

#### Multi-server & Media servers
- **MediaServer type helpers** (`backend/db/media_servers.py`) — `is_plex()`, `is_emby_compatible()`, `server_type()` centralize the `server.get("type") == "plex"` dispatch
- **`ensure_server_uid()`** — auto-populates `server_uid` (Emby/Jellyfin server UUID) on each scan, enabling correct "View on Server" deep links in the public calendar
- **Server-aware deletion** — `_delete_media()` pre-loads `library_id → server_id` map before the deletion loop (eliminates per-item DB query)
- **Seerr cache** built once before the server loop (was rebuilt per-server, N API calls for N servers)
- **Public calendar** — exposes `ext_url` + `server_uid` per server; "View on Server" links use `!/item?id={emby_id}&serverId={server_uid}` for Emby, `!/details?id={emby_id}&serverId={server_uid}` for Jellyfin

#### Plex
- **`PlexClient`** — local API client: libraries, scan, metadata, delete, sessions, search
- **`PlexTVClient`** — cloud API for token validation, friend list, server discovery
- **`/api/plex/webhook`** — multipart endpoint for play/scrobble events (optional secret)
- **Plex expert rules integration** — `_plex_scanner.py` now evaluates expert rules (was hardcoded `view_count == 0 + cutoff`); `_build_plex_item_data()` maps Plex fields to condition schema; fallback to simple logic when no rule matches
- **Plex poster overlays** — "Supprimé dans Xj" banner applied to Plex item posters (`plex_overlay_enabled`)
- **`plex_tv_token`**, **`plex_webhook_secret`** settings fields

#### Expert Rules
- **Multi-library targeting** — `library_ids: list[str]` field; one rule covers libraries from multiple servers
- **Run button per rule** — scans only the rule's targeted libraries (not a full scan)
- **`_build_plex_item_data()`** — maps Plex scan item to expert rule condition schema

#### Security
- **JWT refresh tokens** — `/api/auth/refresh`, `/api/auth/logout`, `/api/auth/logout-all`; auto-rotation with `refresh_tokens` table; 401 interceptor with request queue in frontend
- **`rate_limit()` MariaDB guard** — skips SQLite file I/O on MariaDB deployments (prevents spurious file creation)
- **CORSMiddleware** added explicitly

#### Operations
- **Health endpoint** — dialect-aware: uses `get_db()` + `db.table_exists()` instead of raw aiosqlite; returns `"dialect"` field
- **`healthcheck.py`** — MariaDB-aware: if `DATABASE_URL` is set, skips SQLite file/integrity checks and relies on the HTTP `/health` response
- **`backup.py`** — `if DIALECT != "sqlite": return None` guard with explicit log message
- **`deletion.py`** VACUUM — `if DIALECT == "sqlite":` guard around VACUUM/WAL checkpoint
- **`docker-compose.dev.yml`** — backend hot-reload (`uvicorn --reload`) + Vite dev server proxy

#### Build & CI
- **Vite `manualChunks`** — `vendor-vue` + `vendor-i18n` split; main bundle reduced from 346 KB → 181 KB (-48%)
- **`scripts/check_lm_imports.py`** — verifies all Python files with `lm()` calls have the import
- **`scripts/check_i18n.py`** — validates i18n key consistency across all 8 locale files
- GitHub Actions CI — test + Docker build/push + GitHub release jobs

### Changed

- **`main.py`** reduced from ~550 to ~250 lines; scheduler, public, and version endpoints extracted to dedicated routers
- **`backend/locales/*.json`** — log translations moved from Python dicts to JSON files (one file per language, editible without touching Python)
- **`docker-compose.yml`** — image tag updated to `3.0.0`, MariaDB profile healthcheck uses `${DB_MARIADB_PASSWORD}`; `EMBEDDED_MARIADB` env var documented
- **`version.py`** — default `3.0.0`
- **`_orchestrator.py`** — Seerr cache built once before server loop; `ensure_server_uid()` called per Emby/Jellyfin server at scan time
- **`auth.py` `rate_limit()`** — skips sqlite3 on MariaDB (fallback to in-memory)
- **`routers/expert_rules.py`** — migrate endpoint uses `_migrate_libraries_to_expert_rules_dbconn()` (dialect-aware) instead of raw aiosqlite
- **`main.py _job_next_run`** — uses `get_db()` instead of raw `aiosqlite.connect()`
- **`routers/settings.py`** — uses `is_plex()` helper instead of inline `server.get("type") == "plex"`
- **`deletion.py`** — uses `is_plex()` helper; `server_id` pre-loaded from library map
- **`routers/libraries.py`** — `clone_library` copies `server_id`; uses `_is_plex()` helper
- **`LibrariesTab.vue`** — fully i18n (no hardcoded French strings)
- **`RulesView.vue`** — API calls moved to `stores/rules.js` (migrate + run scan)
- **Public calendar URL** — `/public/<slug>` → `/<slug>` (Vue Router catch-all at end)

### Fixed

- **Scan completely broken** — `_orchestrator.py` and `deletion.py` were missing `from .logmsg import lm` after refactoring; `NameError` on every scan/deletion run
- **`_static_version()`** — removed dead function reading non-existent vanilla `app.js`
- **`clone_library`** — now copies `server_id` (was always defaulting to `'0'`)
- **Double Discord notification** — "detected" + threshold firing simultaneously; fixed by pre-marking applicable thresholds in `_pre_mark_applicable_thresholds()`
- **`added_date` column** — queue view was showing today's date instead of Emby `DateCreated`
- **Discord tab rendering** — vue-i18n `SyntaxError: 10` caused by `@role` in `mentionPlaceholder` locale strings; fixed with `{'@'}` escape syntax
- **`AlertRow` inside `<script setup>`** — extracted to separate `.vue` file to avoid defineComponent context conflict
- **Sidebar collapsible** — `<template v-if>` replaced by `<div v-show>` to fix libraries not displaying
- **Emby colors** — Emby=green, Jellyfin=violet (was reversed)
- **`import aiosqlite`** orphan import in `main.py` removed
- **`rate_limit` on MariaDB** — no longer creates a spurious SQLite file

---

## [2.8.0] — 2026-05-29

### Added
- Pydantic expert rule models (`ConditionField`, `ConditionOp`, `RuleOperator`, `RuleAction`, `Condition`, `ExpertRule`)
- Expert rule evaluation engine (`backend/rules/engine.py`)
- `expert_rules` table + CRUD repositories
- `/api/expert-rules` CRUD endpoints
- Expert rules integrated into the Emby/Jellyfin scanner
- `notifications` table — deduplication for deletion notifications
- Per-library stats and metrics endpoint
- Integration tests for deletion flow

---

## [2.7.0] — 2026-05-28

### Added
- Repository pattern (`backend/db/repositories.py`)
- `_seerr_pages()` async generator for paginated Seerr fetches
- Custom exception hierarchy (`backend/exceptions.py`)

---

## [2.6.0] — 2026-05-28

### Added
- Rate limiting (SQLite-backed, 10 req/min per IP, 500ms cleanup)
- Warning banner when HYGIE_ENCRYPTION_KEY is not set
- API key masking in settings responses
- Global stats moved to dedicated `routers/stats.py`
- `scan_interval_minutes` / `deletion_check_interval_minutes` (migration from hours)
