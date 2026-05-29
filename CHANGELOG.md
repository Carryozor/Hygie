# Changelog

All notable changes to Hygie are documented here.

## [3.0.0] — 2026-05-29

### Added
- **Plex support** — full scan, delete, and webhook integration for Plex Media Server
  - `PlexClient` — local API client (libraries, scan, metadata, delete, sessions, search)
  - `PlexTVClient` — cloud API client for token validation, friend list, server discovery
  - `/api/plex/webhook` — multipart endpoint for play/scrobble events (optional secret)
  - `plex_tv_token` and `plex_webhook_secret` settings fields (Settings UI)
- **MariaDB support** — `DATABASE_URL` env var switches from SQLite to MariaDB/MySQL
  - `DbConn` abstraction layer (`backend/db/engine.py`) — same API for both dialects
  - `backend/tools/migrate_to_mariadb.py` — CLI migration tool (SQLite → MariaDB)
  - MariaDB service in `docker-compose.yml` (profile: `mariadb`)
- **Vue 3 SPA frontend** (replaces legacy Jinja2 templates)
  - Vite 5 + TailwindCSS + Pinia stores
  - Visual rule builder — simple Seerr rules and expert condition builder
  - Multi-condition expert rules with AND/OR connectors, drag handles, logic recap
  - `CreateRuleModal` with type selector → form flow
- **Expert rules visual builder** — `ConditionCard`, `ConnectorPill`, `ExpertRuleBuilder`, `LogicRecap`
- **v2 → v3 data migration** — `_migrate_v2_to_v3()` run once at startup:
  - Backfills `server_id='0'` on pre-v3 libraries
  - Backfills `deletion_unit` defaults
  - Cleans up legacy `emby_url`/`emby_api_key` settings after `media_servers` migration

### Fixed
- **Deletion check interval revert bug** — `schema.py` migration re-ran on every restart when user had set exactly 60 minutes; now uses a one-time guard (`DELETE` old key after migrating)

### Changed
- `backend/db/schema.py` — MariaDB-compatible `init_db()` dispatcher
- `media_queue` — new columns: `plex_rating_key TEXT`, `view_count INTEGER`
- `settings_store.py` — added `plex_tv_token` and `plex_webhook_secret` defaults
- `scanner.py` — routes Plex servers to `_scan_plex_library()`
- `deletion.py` — routes Plex servers to `PlexClient.delete_item()`
- `backend/version.py` — `3.0.0`
- README — updated tagline, features and badge for v3.0.0 + Plex

---

## [2.8.0] — 2025

### Added
- Pydantic expert rule models (`ConditionField`, `ConditionOp`, `RuleOperator`, `RuleAction`, `Condition`, `ExpertRule`)
- Expert rule evaluation engine (`backend/rules/engine.py`)
- `expert_rules` table + CRUD repositories
- `/api/expert-rules` CRUD endpoints
- Expert rules integrated into scanner
- `notifications` table — deduplication for deletion notifications
- Per-library stats metrics
- Integration tests for deletion flow

---

## [2.7.0] — 2025

### Added
- Repository pattern (`backend/db/repositories.py`)
- `_seerr_pages()` async generator for paginated Seerr fetches
- Custom exception hierarchy (`backend/exceptions.py`)

---

## [2.6.0] — 2025

### Added
- Persistent rate limiting via SQLite
- Encryption key warning + API key masking in settings
- `global_stats` moved to `routers/stats.py`
- `scheduler.py` split into focused modules
- Settings live-reload for scan/deletion intervals
- `scripts/check_i18n.py` lint script
