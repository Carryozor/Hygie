# Changelog

All notable changes to Hygie are documented here.

---

## [4.1.0] — 2026-06-18

### Fixed

- **Consolidated season/series deletion left qBittorrent, Sonarr monitoring, and Emby out of sync with what was actually deleted.** When `deletion_unit` is `season` or `series`, Hygie collapses every eligible episode into a single queue entry and bulk-deletes the files via Sonarr — but three separate bugs meant the rest of the stack never caught up:
  - `sonarr_get_torrent_hash()` resolved a qBittorrent hash by grabbing the *first* `downloadId` found anywhere in the series' Sonarr history, regardless of which episode it belonged to. For a season/series spanning more than one download (season packs, per-episode grabs, replaced releases), this could "delete" a torrent unrelated to any of the files actually removed — qBittorrent's delete endpoint returns 200 even for a hash matching no torrent, so Hygie logged success while the real torrent(s) sat untouched on disk. Replaced with `sonarr_get_torrent_hashes_for_group()`, which maps episodeFile → episode → history correctly and returns every distinct hash actually backing the group; `QbitStep` now acts on all of them.
  - `sonarr_delete_series()` / `sonarr_delete_season()` wiped the episode files but left the series/episodes `monitored=true` in Sonarr, so Sonarr could re-grab the now-"missing" episodes on its next RSS sync — silently undoing the deletion. Both now unmonitor after a successful file wipe (series: full series object PUT; season: bulk `episode/monitor` PUT for just that season).
  - `MediaServerStep` skipped Emby entirely for consolidated entries ("synthetic entries have no media server file" — false for a real series), leaving stale library entries behind. It now resolves the real Series/Season item by its on-disk path (`emby_client.find_item_by_path()`, exact-path-verified) and deletes it.
- **`_delete_from_arr()` could misroute a single per-episode deletion into a whole-season wipe.** A normal `deletion_unit=episode` row carries `sonarr_id` (its own episode file id) *and* `sonarr_series_id`/`season_number` — the scanner sets all three together for every regular episode — so the season/series branches (keyed only on `sonarr_series_id`/`season_number`) could fire for a single-episode delete and call `sonarr_delete_season()`, removing every file in that entire season. Found via code review while fixing the above; the dispatcher now requires `sonarr_id` to be absent before treating a row as consolidated.

### Tests

- New regression coverage: `tests/test_sonarr_consolidated_deletion.py`, `tests/test_deletion_pipeline_consolidated.py`, `tests/test_delete_from_arr_dispatch.py`, plus `find_item_by_path()` cases in `tests/test_emby_client.py`.

---

## [4.0.6] — 2026-06-18

### Security

- **MariaDB rate limiting no longer falls back to per-process memory** — in multi-worker deployments (the documented production topology), failed-login rate limiting silently used an in-memory counter per worker instead of the shared `rate_limit` table, multiplying the effective limit by the worker count and resetting it on every restart. Now backed by the database on MariaDB too, in its own module (`backend/_rate_limit_backend.py`) so the raw-driver `%s` placeholders stay out of the dialect-translated query layer.
- **Image proxy: the DNS-rebinding check now re-runs on every redirect hop**, not just the initial request — a whitelisted host's DNS could be rebound between the first request and a followed redirect.
- **Emby circuit breaker wiring completed** — `get_library_user_data` and `get_play_activity` (both scan-critical) now route through the same per-server circuit breaker as the rest of the Emby client, matching the documented architecture and avoiding unbounded retry storms when Emby is down mid-scan.
- **REPLACE INTO replaced with INSERT ... ON DUPLICATE KEY UPDATE for MariaDB** — `REPLACE INTO` does DELETE+INSERT internally; under REPEATABLE-READ with multiple Uvicorn workers, concurrent transactions on the settings table caused `ER_READ_CHANGED_ROW` (error 1020). Also adds rollback in `get_db()` so failed MariaDB transactions are cleaned up before the connection returns to the pool.
- **Frontend: access token moved out of localStorage** — it now lives in memory only (`api/tokenStore.js`) and is re-minted from the httpOnly refresh cookie on page load via a silent `/auth/refresh` call, closing the gap where any injected script could read it from localStorage.
- **Frontend: dynamic `:href` bindings now validate the URL scheme** (`utils/safeUrl.js`) before rendering — Vue does not auto-escape `:href`, so an unsanitized `javascript:` URL in a Seerr request link or media-server URL would have executed in the authenticated origin.
- **Legacy fallback UI (`frontend/static/js/libraries.js`)**: two library-name interpolations were unescaped (a `data-n` attribute and an `<option>` body) — a stored DOM-XSS path, now escaped consistently with the rest of the file.
- **Dependency: `npm audit fix`** — patched the `form-data` CRLF-injection advisory pulled in transitively via axios.
- **Dockerfile: dashboard icon downloads are now sha256-verified**, matching the integrity checking already in place for the bundled Font Awesome assets.

### Fixed

- **`CREATE INDEX IF NOT EXISTS` on MariaDB** — that syntax only exists on MariaDB ≥ 10.5 and is a hard syntax error on MySQL and older MariaDB, which could break schema init outright on those versions. Indexes are now created unconditionally with the "already exists" error tolerated in code, giving the same idempotency across every MariaDB/MySQL version.
- **New indexes**: composite `media_queue(status, delete_at)` (the hottest query in the app) and `libraries(server_id)`.
- **Seerr/Jellyseerr pagination** — `_seerr_pages` no longer raises `AttributeError` if a Seerr instance returns a bare JSON array instead of `{results, pageInfo}` (seen on some older/proxied variants); it now treats that page as the final one instead of aborting the whole request-cache build.
- **Deletion stats for multi-Radarr/Sonarr setups** — `SizeLookupStep` queried only the legacy default server for file size, so it could silently report 0 (or the wrong movie/series) when `radarr_id`/`sonarr_series_id` belonged to a different configured server. New `radarr_get_any()` / `sonarr_get_series_by_id_any()` helpers check every configured server, the same way `radarr_get_torrent_hash_any()` already did.
- **Deletion: exceptions escaping `asyncio.gather` are no longer dropped silently** — they're now logged and counted as errors instead of just being absent from the success count.
- Minor robustness fixes: `overlay.py` used the deprecated `asyncio.get_event_loop()`; `collection.py` could raise on a malformed Emby `/Users` response; `services/arr_service.py` swallowed the masked-API-key lookup error with a bare `pass` instead of logging it; `db/repositories.py` no longer silently swallows a failed rollback in the batch-insert error path.

### Frontend

- Removed the "X au total" sub-label under the dashboard's "En attente" stat — it always echoed the same total as the pending count and added no information.
- 429 (rate-limited) API responses are now surfaced to the user as a toast instead of failing silently.

### Tests

- Added a `_reset_circuit_breakers` autouse fixture (`tests/conftest.py`) — the circuit-breaker registry is process-global and wasn't reset between tests, so a breaker tripped OPEN by one test's failure simulation could leak into an unrelated later test sharing the same service name.

---

## [4.0.5] — 2026-06-15

### Security

- **HSTS header added** — `Strict-Transport-Security: max-age=63072000; includeSubDomains` is now sent by the application layer so that direct access (without Cloudflare/Nginx in front) is also protected.
- **Public dashboard: password no longer accepted via query parameter** — the `?password=` query param was leaking the credential in reverse-proxy and Cloudflare access logs. Password must now be supplied via the `X-Dashboard-Password` HTTP header.
- **Plex webhook: path-based endpoint added** — a new `POST /api/plex/webhook/{token}` endpoint is available as the recommended URL for new Plex webhook installations. The legacy `?secret=` form remains for backward compatibility. Both still appear in access logs — use HTTPS (Cloudflare tunnel) and restrict log access.
- **Refresh token removed from JSON response body** — `login`, `setup`, and `change-password` no longer return `refresh_token` in the JSON body. The token is delivered exclusively via the httpOnly cookie (`hygie_refresh`), preventing XSS exfiltration from JavaScript. The `/api/auth/refresh` endpoint still accepts a body token as fallback for programmatic clients.
- **Backup path validation** — `backup_path` in settings now rejects paths targeting system directories (`/etc`, `/root`, `/proc`, `/sys`, `/dev`, etc.) to prevent an authenticated admin from using the backup write path as a filesystem primitive.
- **jobs/history limit bounded** — `GET /api/jobs/history?limit=` is now capped at 1 000 (was unbounded); requests with higher values return 422.
- **Startup warning: HYGIE_TRUST_PROXY not set with public origins** — Hygie now logs a WARN at startup when `HYGIE_ALLOWED_ORIGINS` contains a non-localhost domain but `HYGIE_TRUST_PROXY` is not enabled. Without this, rate limiting uses the shared proxy IP instead of real client IPs.
- **qBittorrent compose healthcheck** — the Docker Compose healthcheck for the `qbittorrent` service no longer embeds `${QB_PASSWORD}` in the command (visible via `docker inspect`); replaced with a credential-free HTTP connectivity check.

---

## [3.6.3] — 2026-06-11

### Fixed

- **Discord IDs from Seerr came back empty for every user** — recent Seerr/Jellyseerr versions replaced the single `discordId` string with a `discordIds` list in `/api/v1/user/{id}/settings/notifications`; Hygie still read the legacy field, so auto-detected Discord IDs (used for mentions in deletion notifications) silently disappeared. The new list format is now read (first entry wins), with fallback to the legacy `discordId` string for older Seerr versions. Affects both the Seerr users listing (`seerr_get_users`) and the per-user mention resolution (`_resolve_discord_id`).

---

## [3.6.2] — 2026-06-11

### Fixed

- **Series were silently excluded from scans whenever a Seerr user filter was active (CRITICAL)** — `seerr_user_id` was resolved from the item's own `ProviderIds.Tmdb`, but Emby/Jellyfin **Episode** items never carry the series-level TMDB id (only episode Tvdb/Imdb ids), while the Seerr request cache is keyed by the series `tmdbId`. The lookup therefore always returned `None` for episodes, and any expert rule with a `seerr_user_id IN […]` condition or library `user_include`/`user_exclude` filter excluded **all series** — movies were unaffected. The scanner now builds a per-library `{SeriesId → series tmdb}` map (fetched lazily on the first episode encountered, circuit-breaker protected, fail-soft) and resolves episodes through their parent series. Queue entries for episodes now also store the series TMDB id, fixing the Seerr request link and the poster fallback for series.

---

## [3.6.1] — 2026-06-10

### Fixed

- **MariaDB mode was broken for every runtime write (CRITICAL)** — MariaDB is documented as fully supported, but the database initialised and then failed on the first write. Three independent bugs, none caught because no test ever wrote to a live MariaDB (the schema tests are parse-only and the migration tests are dry-run):
  - **SQLite-only upsert syntax** — `INSERT OR REPLACE` / `INSERT OR IGNORE` raise `ERROR 1064` on MariaDB. `DbConn` now rewrites them to `REPLACE` / `INSERT IGNORE`. This affected saving any setting, saving media servers, ignoring media, and threshold notifications.
  - **Reserved word `key`** — the `settings.key` column was referenced unquoted in runtime queries (`settings_store`, `encryption`, `media_servers`, `migrations`), a syntax error on MariaDB. Now backtick-quoted (valid on both dialects).
  - **Startup ordering** — `init_db()` and `run_migrations()` ran before `init_db_pool()`, so `get_db()` raised "pool not initialized" on MariaDB before any table was created. The pool is now initialised first; schema/migrations are skipped (with a clean CRITICAL) if it fails.
  - Literal `%` (LIKE wildcards baked into SQL) is now doubled to `%%` so aiomysql's printf-style query formatting doesn't fail.
  - A live MariaDB service was added to CI so these write paths are exercised on every run.
- **qBittorrent torrent matched by name substring** (`qbit_client.py`) — `qbit_find_by_path` matched a torrent whenever its name was a substring of the file path (e.g. torrent "Dune" matched `/movies/Dune Part Two/…`). Since the resulting hash is used to tag or delete the torrent, this could act on the wrong torrent. Matching is now exact on `content_path` or file-inside-folder only.

### Security

- **Public dashboard password no longer sent in the query string** (`PublicView.vue`) — the password was passed as `?password=…`, leaking into server access logs, browser history and the Referer header. It now travels in the `X-Dashboard-Password` header (already supported server-side).

---

## [3.6.0] — 2026-06-10

### Security

- **SSRF hardening of the image proxy** (`proxy.py`) — The whitelist now matches `(host, port)` instead of hostname alone, closing a port-scanning oracle through whitelisted hosts. Redirects are followed manually (max 3 hops) with every hop re-validated against the whitelist — a whitelisted host can no longer redirect the proxy to an internal target. The poster proxy no longer follows redirects at all.
- **Plex webhook is now fail-closed** (`plex_webhook.py`) — Events are rejected with 403 until `plex_webhook_secret` is configured; previously an unconfigured secret accepted any caller, letting anyone forge scrobble events and shift `last_played` to delay or prevent deletions. Secret comparison now uses `secrets.compare_digest` (constant-time). **Action required:** if you use the Plex webhook, set a secret in Settings and append `?secret=<value>` to the webhook URL in Plex.
- **Refresh token moved to an httpOnly cookie** (`routers/auth.py`, frontend) — The 30-day refresh token no longer touches `localStorage` where any XSS could read it. It is delivered as an `httpOnly` `SameSite=Strict` cookie scoped to `/api/auth` (`Secure` on HTTPS), **rotated on every refresh** with the previous token retired after a 60-second grace window for concurrent tabs. Existing sessions are migrated transparently; the JSON body field is kept one release for backward compatibility.
- **LIKE wildcard injection in search inputs** (`media.py`, `ignored.py`, `logs.py`) — User search strings containing `%` or `_` are now escaped (`ESCAPE '!'`, portable across SQLite and MariaDB).
- **CDN asset integrity at build time** (`Dockerfile`) — Font Awesome CSS and fonts are pinned by sha256; dashboard icons are pinned to an immutable upstream commit; any failed or tampered download now fails the build instead of being silently ignored.

### Fixed

- **Dry-run no longer marks items as deleted** (`deletion.py`, `media.py`) — Both the scheduled deletion job and the delete-now endpoint previously set `status='deleted'` in dry-run mode without deleting any file, permanently removing the item from the pipeline. Dry-run now simulates only: items stay `pending`, and delete-now returns `{"status": "dry_run"}`.
- **Double-deletion race between the deletion job and delete-now** (`deletion.py`) — The scheduled job now claims each item atomically (`pending → deleting`) before deleting, like the endpoint already did. Items left stuck in `deleting` by a crash mid-deletion are recovered to `pending` at startup.
- **Sidebar scan/deletion countdowns missing after login** (`App.vue`) — `status.start()` only ran on mount, which never re-executes on SPA navigation; after a fresh login the scheduler polling never started until a full page reload. A watcher on the login state now starts/stops polling correctly.
- **`fetch_one` corrupted non-SELECT statements** (`db/engine.py`) — `LIMIT 1` was appended to any statement lacking the word LIMIT, breaking `PRAGMA` queries and misfiring on literals containing "limit". It now applies only to SELECT/CTE statements, checked outside string literals.
- **`database is locked` under parallel library scans** (`db/engine.py`) — SQLite connections now set `PRAGMA busy_timeout=5000`.
- **Event-loop stalls during authentication** (`routers/auth.py`, `auth.py`) — Argon2 verification (~170 ms CPU) and the synchronous SQLite rate limiter now run in worker threads via `asyncio.to_thread`; the in-memory rate-limit fallback is protected by a lock for thread safety.

### Changed

- **Single rule evaluation engine** (`rules/`) — Per-library legacy conditions are now converted on the fly into expert rules and evaluated by `rules/engine.py`, the same engine used by expert rules on Emby/Jellyfin and Plex. Legacy edge-case semantics are preserved and pinned by tests (never-watched items always satisfy `days_not_watched`; unknown fields evaluate to false). The duplicated comparison logic in `legacy_conditions.py` is gone.
- **Circuit breakers fully wired** (`arr_clients/`, `db/utils.py`) — Radarr, Sonarr and Seerr calls now route through their circuit breakers via `with_retry(service=...)` / the `seerr` breaker (Emby and Plex were already wired). When a service is down, scans fast-fail instead of hammering it; breaker states are visible in `/health` under `circuit_breakers`.
- **Reproducible frontend builds** (`Dockerfile`) — `npm install` replaced by `npm ci`.

### Added

- **`never_watched` as a first-class expert rule field** — Usable in the expert rule builder (all 8 languages), populated by both the Emby/Jellyfin and Plex scanners (`1`/`0`, operator `=`).

---

## [3.4.3] — 2026-06-09

### Security

- **API keys in query parameters** (`unmonitored.py`, `storage.py`) — Radarr and Sonarr API keys were appended to URLs as `?apikey=…`, making them visible in proxy and server access logs. Replaced with `X-Api-Key` header via the existing `_arr_auth()` helper.
- **MySQL password exposed in process list** (`backup.py`) — `mysqldump` was called with `--password=<value>`, making the password visible in `ps aux` and `/proc`. Now writes credentials to a `0600` temp file passed via `--defaults-extra-file`, deleted in a `finally` block.
- **Unauthenticated Prometheus `/metrics` endpoint** (`metrics.py`) — Protected by an optional Bearer token. If `prometheus_bearer_token` is configured in settings the header is required; otherwise the endpoint stays open (backward-compatible).
- **Dashboard password in query parameter** (`public.py`) — The public dashboard password can now also be supplied via the `X-Dashboard-Password` HTTP header as an alternative to the query param.
- **Missing `public_dashboard_enabled` default** (`settings_store.py`) — The key was absent from `DEFAULT_SETTINGS`, causing a `KeyError` on fresh installs before the setting was explicitly saved.

### Fixed

- **Emby collection image not refreshing after overlay sync** (`collection.py`) — The collection mosaic was being overwritten by an internet-fetched poster because `ReplaceAllImages=true` was passed to the Refresh endpoint. Fixed by setting `ReplaceAllImages=false` so Emby regenerates the mosaic from the freshly-uploaded overlaid item posters. Added a 1-second pause before the refresh to let Emby commit uploaded images, changed `MetadataRefreshMode` from `None` to `Default`, and added warning-level logging on unexpected HTTP status codes.
- **Emby item poster upload sending base64 string instead of bytes** (`_emby_scanner.py`) — Poster content was encoded to a base64 ASCII string before uploading. The Emby Images API expects raw binary; fixed by passing `pr.content` (bytes) directly.
- **qBittorrent `delete_files` action not recognized** (`deletion.py`) — The value `delete_files` was not handled alongside `delete_torrent`, causing the deletion logic to silently do nothing. Both values now trigger the delete path.
- **Double-delete race condition in `DELETE /media/{id}/delete-now`** (`media.py`) — Two concurrent requests could both pass the `SELECT … WHERE status=pending` check and trigger deletion twice. Fixed with an atomic `UPDATE … SET status='deleting' WHERE status='pending'` using `execute_write()` rowcount check.

### Performance

- **Sequential per-user notification fetches in `seerr_get_users`** — Replaced with `asyncio.gather` + `Semaphore(10)`.
- **Sequential episode-file fetches in `build_sonarr_path_cache`** — Replaced with `asyncio.gather` + `Semaphore(8)`.
- **N+1 DB writes in `enrich_seerr` and `regenerate_posters`** — All updates batched into a single `get_db()` context after the loop.

---

## [3.4.2] — 2026-06-06

### Fixed

- **qBittorrent v5+ authentication** (`qbit_client.py`) — qBit v5 with bypass-auth returns HTTP 204 (empty body) instead of `200 "Ok."`. Auth now accepts both; cookie detection extended to `QBT_SID_<PORT>` naming used by v5+.
- **Double "v" prefix in qBit version string** — Version was displayed as `vv5.2.1`; fixed to `v5.2.1`.
- **Missing version label on proxy UI test result** — qBit version is now shown in the proxy test feedback.
- **`_sid_cookie` could be `None` when bypass-auth returns no cookie** — Request was built with `{"SID": None}`; fixed to send an empty cookie dict when no SID is present.

### Added

- **Credential reveal endpoint** (`GET /api/settings/reveal/{key}`) — Returns the decrypted plaintext value of a stored credential for display in the UI.
- **Eye button in settings tabs** — Radarr, Sonarr, Seerr, and qBittorrent tabs now have a reveal button to display stored credentials (uses `useRevealSetting` composable).

### Refactored

- Extracted `_extract_sid()` helper in `qbit_client.py` for SID / `QBT_SID_*` cookie detection.

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