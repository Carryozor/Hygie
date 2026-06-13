<div align="center">

<img src="frontend/static/img/logo.svg" width="96" alt="Hygie">

# Hygie

**Smart media library manager for Emby, Jellyfin & Plex**  
**Gestionnaire intelligent de bibliothèque média pour Emby, Jellyfin & Plex**

*Open-source alternative to Maintainerr*

[![Docker](https://img.shields.io/badge/Docker-ghcr.io%2Fcarryozor%2Fhygie-blue?logo=docker&logoColor=white)](https://github.com/carryozor/hygie/pkgs/container/hygie)
[![Version](https://img.shields.io/badge/version-4.0.2-brightgreen)](https://github.com/carryozor/hygie/releases)
[![CI](https://github.com/carryozor/hygie/actions/workflows/ci.yml/badge.svg)](https://github.com/carryozor/hygie/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python)](https://www.python.org/)
[![Vue](https://img.shields.io/badge/Vue-3.x-4FC08D?logo=vue.js)](https://vuejs.org/)

---

🇬🇧 [English](#english) · 🇫🇷 [Français](#français)

</div>

---

## English

### What is Hygie?

Hygie automatically scans your **Emby, Jellyfin or Plex** media libraries, identifies unused media based on configurable rules, and orchestrates their deletion across your entire *arr stack — Radarr, Sonarr, Overseerr/Jellyseerr, and qBittorrent — while keeping your users informed via Discord.

### ✨ Features

#### 🖥️ Interface — Vue 3
- Modern **Vue 3 + Vite** single-page application
- **8 languages**: French, English, German, Spanish, Italian, Portuguese, Dutch, Polish
- Real-time log stream via WebSocket (DB-poll, safe in multi-worker mode)
- Collapsible server/library tree in sidebar
- Global **dry-run toggle** from the sidebar

#### 🎬 Multi-Server (Emby, Jellyfin & Plex)
- **Automatic type detection** via `/System/Info` (`ProductName` + version heuristics)
- **Multiple servers** — configure several Emby/Jellyfin/Plex simultaneously
- **Plex support** — scan libraries, detect unwatched, delete via local API
- **Plex webhooks** — receive scrobble events to update `last_played` without a full scan
- Per-server color coding: Emby (green), Jellyfin (purple), Plex (orange)
- Auto-populated `serverId` for direct media links in the public calendar
- **"Leaving soon" collections** — Emby/Jellyfin smart collection for media near deletion, with optional overlay

#### 🎯 Expert Rules Engine
- **Visual condition builder** with AND/OR operators between condition groups
- Fields: days not watched, play count, community rating, file size (GB), added days ago, media type, Seerr user, never watched
- **Multi-library targeting** — one rule can cover libraries from multiple servers
- **Actions**: queue for deletion or notify-only
- Per-rule grace period override
- Simple Seerr rules: per-user grace periods with optional Discord ID mapping
- One-click scan trigger per rule, filtered to the rule's libraries only

#### 🔔 Discord Notifications
- Configurable threshold alerts (e.g. 7d, 1d before deletion) + at-deletion
- Automatic **@mention** of the requester (via Seerr mapping or Discord ID)
- Dual webhook support: main (deletions) + alerts (errors, failures)
- Separate alerts for: deletion failures, scan failures, Seerr unreachable
- Error threshold: batch alert when N consecutive failures occur

#### 🗑️ Orchestrated Deletion Pipeline
Full deletion pipeline executed in sequence:
1. Discord notification (while media is still accessible for the poster)
2. Emby / Jellyfin / Plex — remove item
3. Radarr / Sonarr — remove from library (files deleted)
4. Overseerr / Jellyseerr — delete request
5. qBittorrent — tag or delete torrent (configurable)

#### 📅 Public Calendar
- Share upcoming deletions without requiring login
- Optional **password protection** and custom **URL slug** (`/myslug`)
- **Language selector** (8 languages, persists in localStorage)
- Filter by server, grouped by server → library
- **"View on Server"** link per media (with correct `serverId`)

#### 🗄️ Database
- **SQLite** by default — zero configuration
- **MariaDB external** — set `DATABASE_URL` env var
- **MariaDB embedded** — single-container mode (`EMBEDDED_MARIADB=true`)
- **MariaDB container** — via `docker compose --profile mariadb`
- **Bidirectional migration UI** — SQLite → MariaDB and MariaDB → SQLite from the Settings page
- Dialect-aware health check, rate limiting, backup, and VACUUM

#### ⚡ Multi-Worker
- Set `WORKERS=N` to run N uvicorn processes (requires MariaDB)
- **MariaDB advisory locks** (`GET_LOCK` / `RELEASE_LOCK`) — only one worker runs each scan or deletion cycle; others skip silently
- WebSocket log streaming uses **DB polling** — clients on any worker receive all logs
- Startup validation blocks misconfiguration (`WORKERS>1` without MariaDB/advisory lock)

#### 📊 Observability
- **Prometheus metrics** at `/metrics` (optional bearer token) and JSON at `/api/metrics`
- **Storage dashboard** — disk usage per Radarr/Sonarr instance (stale-while-revalidate)
- **Unmonitored items** — list Radarr/Sonarr entries that are unmonitored but still have files
- **Circuit breakers** — Emby, Plex, Radarr, Sonarr, Seerr; `/health` reports `degraded` when any breaker is open
- Structured logging with configurable level and retention

#### 🛡️ Security
- Argon2id password hashing
- JWT HS256 with refresh tokens (auto-rotation)
- Rate limiting on all endpoints
- SSRF-protected image proxy with domain whitelist
- **Fernet encryption at rest** for sensitive settings (API keys, webhooks)
- Strict HTTP headers (HSTS, X-Frame-Options, CSP)

#### 🔧 Operations
- APScheduler with countdown preservation across restarts
- **SQLite backup** with configurable interval and retention (SQLite only)
- Automatic VACUUM + WAL checkpoint after large purges (SQLite only)
- Dedicated `/health` endpoint for Uptime Kuma / Docker HEALTHCHECK

---

### 🚀 Quick Start

#### Option 1 — SQLite (default, zero config)

```yaml
# docker-compose.yml
services:
  hygie:
    image: ghcr.io/carryozor/hygie:latest
    container_name: hygie
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
    environment:
      - TZ=Europe/Paris
      - HYGIE_ENCRYPTION_KEY=${HYGIE_ENCRYPTION_KEY:-}
```

```bash
docker compose up -d
# Open http://localhost:8000 → Setup wizard
```

#### Option 2 — External MariaDB

```yaml
environment:
  - DATABASE_URL=mysql+aiomysql://user:pass@host:3306/hygie
```

#### Option 3 — Embedded MariaDB (all-in-one)

```bash
# Build with MariaDB support (~350 MB larger image)
docker compose -f docker-compose.yml -f docker-compose.embedded-mariadb.yml build

# Start (runs as root to manage mysqld)
docker compose -f docker-compose.yml -f docker-compose.embedded-mariadb.yml up -d
```

#### Option 4 — Separate MariaDB container

```bash
docker compose --profile mariadb up -d
# Set DATABASE_URL=mysql+aiomysql://hygie:${DB_MARIADB_PASSWORD}@mariadb:3306/hygie
```

#### Option 5 — Multi-worker (MariaDB required)

```yaml
environment:
  - DATABASE_URL=mysql+aiomysql://user:pass@host:3306/hygie
  - HYGIE_LOCK_BACKEND=mariadb
  - WORKERS=2
```

---

### ⚙️ Configuration

Copy `.env.example` to `.env`:

```bash
# Encryption key — recommended for production
# Generate: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
HYGIE_ENCRYPTION_KEY=

# Database (leave empty for SQLite)
DATABASE_URL=

# Multi-worker (requires MariaDB)
WORKERS=1
HYGIE_LOCK_BACKEND=asyncio   # set to "mariadb" when WORKERS > 1

# MariaDB separate container
DB_MARIADB_PASSWORD=change_me
DB_MARIADB_ROOT_PASSWORD=change_me_root
```

All other settings (media servers, Radarr/Sonarr/Seerr URLs, Discord webhooks, scan intervals, etc.) are configurable from the **Settings** page in the UI.

---

### 📦 Stack Requirements

| Service | Required | Notes |
|---------|----------|-------|
| Emby / Jellyfin / Plex | ✅ | At least one media server |
| Radarr | Optional | Movie library management (multi-instance supported) |
| Sonarr | Optional | TV library management (multi-instance supported) |
| Overseerr / Jellyseerr | Optional | Request tracking, per-user rules |
| qBittorrent | Optional | Torrent tag or delete |
| Discord | Optional | Notifications and alerts |

---

### 📊 Health Check

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "healthy",
  "version": "4.0.2",
  "timestamp": "2026-06-13T14:00:00.000000+00:00",
  "database": "ok",
  "scheduler": "3 jobs",
  "disk": "ok",
  "encryption": "enabled"
}
```

When circuit breakers are active, the response includes a `circuit_breakers` field and `status` becomes `degraded`.

---

### 🛠️ Development

```bash
# Backend hot-reload + Vite dev server
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# Frontend Vite dev server (separate terminal)
cd frontend/vue && npm run dev
# UI at http://localhost:5173 (proxied to :8000)
```

```bash
# Run backend tests
pytest tests/ -q

# Check lm() imports (prevents scan breakage)
python3 scripts/check_lm_imports.py

# Check i18n key consistency
python3 scripts/check_i18n.py
```

---

### 🔄 Migrating to MariaDB

Use the **Settings → Database** page for an in-UI migration, or run the CLI tool directly:

```bash
docker exec hygie python3 -m backend.tools.migrate_to_mariadb \
  --sqlite-path /app/data/hygie.db \
  --database-url "mysql+aiomysql://hygie:PASSWORD@mariadb:3306/hygie"
```

> **Important:** run the migration **before** starting Hygie against the new MariaDB, or immediately after (Hygie inserts empty defaults at startup; the migration tool uses `REPLACE INTO` to overwrite them with the real values).

---

## Français

### Qu'est-ce que Hygie ?

Hygie analyse automatiquement vos bibliothèques média **Emby, Jellyfin ou Plex**, identifie les médias inutilisés selon des règles configurables, et orchestre leur suppression sur toute votre stack *arr — Radarr, Sonarr, Overseerr/Jellyseerr, qBittorrent — en informant vos utilisateurs via Discord.

### ✨ Fonctionnalités

#### 🖥️ Interface — Vue 3
- Application **Vue 3 + Vite** moderne
- **8 langues** : français, anglais, allemand, espagnol, italien, portugais, néerlandais, polonais
- Flux de logs en temps réel via WebSocket (DB-poll, safe en multi-worker)
- Arbre serveur/bibliothèque rétractable dans la sidebar
- **Toggle dry-run global** depuis la sidebar

#### 🎬 Multi-serveur (Emby, Jellyfin & Plex)
- **Détection automatique** du type via `/System/Info` (heuristiques `ProductName` + version)
- **Plusieurs serveurs** — Emby/Jellyfin/Plex configurables simultanément
- **Support Plex** — scan bibliothèques, détection non-regardé, suppression via API locale
- **Webhooks Plex** — réception des événements scrobble pour mettre à jour `last_played` sans scan complet
- Couleurs par type : Emby (vert), Jellyfin (violet), Plex (orange)
- `serverId` auto-peuplé pour les liens directs dans le calendrier public
- **Collections « bientôt supprimé »** — collection Emby/Jellyfin pour les médias proches de la suppression, avec overlay optionnel

#### 🎯 Moteur de règles expertes
- **Constructeur visuel** avec opérateurs AND/OR entre groupes de conditions
- Champs : jours sans visionnage, nombre de lectures, note communautaire, taille (Go), jours depuis ajout, type de média, utilisateur Seerr, jamais regardé
- **Ciblage multi-bibliothèques** — une règle peut couvrir des bibliothèques de plusieurs serveurs
- **Actions** : mettre en file de suppression ou notifier seulement
- Délai de grâce configurable par règle
- Règles simples Seerr : délais par utilisateur avec Discord ID optionnel
- Déclenchement de scan par règle en un clic, limité aux bibliothèques de la règle

#### 🔔 Notifications Discord
- Seuils configurables (ex. 7j, 1j avant suppression) + à la suppression
- **@mention** automatique du demandeur (via mapping Seerr ou Discord ID)
- Double webhook : principal (suppressions) + alertes (erreurs)
- Alertes dédiées : échecs suppression, échecs scan, Seerr inaccessible
- Seuil d'erreurs : alerte groupée après N échecs consécutifs

#### 🗑️ Pipeline de suppression orchestré
Pipeline exécuté dans l'ordre :
1. Notification Discord (affiche encore accessible)
2. Emby / Jellyfin / Plex — suppression
3. Radarr / Sonarr — retrait (fichiers supprimés)
4. Overseerr / Jellyseerr — suppression requête
5. qBittorrent — tag ou suppression torrent

#### 📅 Calendrier public
- Partagez les suppressions à venir sans login
- **Protection par mot de passe** et **slug URL personnalisé** (`/monslug`)
- **Sélecteur de langue** (8 langues, mémorisé en localStorage)
- Filtre par serveur, groupé serveur → bibliothèque
- Lien **« Voir sur Serveur »** par média (avec `serverId` correct)

#### 🗄️ Base de données
- **SQLite** par défaut — zéro configuration
- **MariaDB externe** — variable `DATABASE_URL`
- **MariaDB embarquée** — mode tout-en-un (`EMBEDDED_MARIADB=true`)
- **Conteneur MariaDB** — via `docker compose --profile mariadb`
- **Migration bidirectionnelle** SQLite ↔ MariaDB depuis la page Paramètres
- Health check, rate limiting, backup et VACUUM dialect-aware

#### ⚡ Multi-Worker
- Variable `WORKERS=N` pour démarrer N processus uvicorn (nécessite MariaDB)
- **Verrous advisories MariaDB** (`GET_LOCK` / `RELEASE_LOCK`) — un seul worker exécute chaque cycle de scan ou suppression ; les autres passent silencieusement
- Streaming de logs WebSocket via **DB-poll** — les clients sur n'importe quel worker reçoivent tous les logs
- Validation au démarrage : bloque la mauvaise configuration (`WORKERS>1` sans MariaDB)

#### 📊 Observabilité
- **Métriques Prometheus** sur `/metrics` (token Bearer optionnel) et JSON sur `/api/metrics`
- **Dashboard stockage** — utilisation disque par instance Radarr/Sonarr (cache stale-while-revalidate)
- **Médias non-monitorés** — liste des entrées Radarr/Sonarr non-monitorées avec fichiers présents
- **Circuit breakers** — Emby, Plex, Radarr, Sonarr, Seerr ; `/health` passe à `degraded` quand un breaker est ouvert
- Logs structurés avec niveau et rétention configurables

#### 🛡️ Sécurité
- Hashage de mots de passe Argon2id
- JWT HS256 avec tokens de rafraîchissement (rotation automatique)
- Rate limiting sur tous les endpoints
- Proxy image protégé contre les SSRF avec whitelist de domaines
- **Chiffrement Fernet au repos** pour les paramètres sensibles (clés API, webhooks)
- En-têtes HTTP stricts (HSTS, X-Frame-Options, CSP)

#### 🔧 Opérations
- APScheduler avec préservation du countdown entre redémarrages
- **Backup SQLite** avec intervalle et rétention configurables (SQLite uniquement)
- VACUUM + WAL checkpoint automatique après de grosses suppressions (SQLite uniquement)
- Endpoint `/health` dédié pour Uptime Kuma / Docker HEALTHCHECK

---

### 🚀 Démarrage rapide

#### Option 1 — SQLite (défaut, zéro config)

```yaml
services:
  hygie:
    image: ghcr.io/carryozor/hygie:latest
    container_name: hygie
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
    environment:
      - TZ=Europe/Paris
      - HYGIE_ENCRYPTION_KEY=${HYGIE_ENCRYPTION_KEY:-}
```

```bash
docker compose up -d
# Ouvrir http://localhost:8000 → Assistant de configuration
```

#### Option 2 — MariaDB externe

```yaml
environment:
  - DATABASE_URL=mysql+aiomysql://user:pass@host:3306/hygie
```

#### Option 3 — MariaDB embarquée (tout-en-un)

```bash
docker compose -f docker-compose.yml -f docker-compose.embedded-mariadb.yml build
docker compose -f docker-compose.yml -f docker-compose.embedded-mariadb.yml up -d
```

#### Option 4 — Conteneur MariaDB séparé

```bash
docker compose --profile mariadb up -d
# Définir DATABASE_URL=mysql+aiomysql://hygie:${DB_MARIADB_PASSWORD}@mariadb:3306/hygie
```

#### Option 5 — Multi-worker (MariaDB requis)

```yaml
environment:
  - DATABASE_URL=mysql+aiomysql://user:pass@host:3306/hygie
  - HYGIE_LOCK_BACKEND=mariadb
  - WORKERS=2
```

---

### ⚙️ Configuration

Copier `.env.example` en `.env` :

```bash
# Clé de chiffrement — recommandée en production
# Générer : python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
HYGIE_ENCRYPTION_KEY=

# Base de données (vide = SQLite)
DATABASE_URL=

# Multi-worker (nécessite MariaDB)
WORKERS=1
HYGIE_LOCK_BACKEND=asyncio   # passer à "mariadb" si WORKERS > 1

# MariaDB conteneur séparé
DB_MARIADB_PASSWORD=changer_moi
DB_MARIADB_ROOT_PASSWORD=changer_moi_root
```

Tous les autres paramètres (serveurs média, URLs Radarr/Sonarr/Seerr, webhooks Discord, intervalles de scan, etc.) sont configurables depuis la page **Paramètres** de l'interface.

---

### 📦 Prérequis stack

| Service | Requis | Notes |
|---------|--------|-------|
| Emby / Jellyfin / Plex | ✅ | Au moins un serveur média |
| Radarr | Optionnel | Gestion bibliothèque films (multi-instances supporté) |
| Sonarr | Optionnel | Gestion bibliothèque séries (multi-instances supporté) |
| Overseerr / Jellyseerr | Optionnel | Suivi requêtes, règles par utilisateur |
| qBittorrent | Optionnel | Tag ou suppression torrent |
| Discord | Optionnel | Notifications et alertes |

---

### 📊 Health Check

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "healthy",
  "version": "4.0.2",
  "timestamp": "2026-06-13T14:00:00.000000+00:00",
  "database": "ok",
  "scheduler": "3 jobs",
  "disk": "ok",
  "encryption": "enabled"
}
```

Quand des circuit breakers sont actifs, la réponse inclut un champ `circuit_breakers` et `status` passe à `degraded`.

---

### 🔄 Migration vers MariaDB

Utilisez la page **Paramètres → Base de données** pour une migration depuis l'interface, ou l'outil CLI directement :

```bash
docker exec hygie python3 -m backend.tools.migrate_to_mariadb \
  --sqlite-path /app/data/hygie.db \
  --database-url "mysql+aiomysql://hygie:MOT_DE_PASSE@mariadb:3306/hygie"
```

> **Important :** lancez la migration **avant** de démarrer Hygie sur le nouveau MariaDB, ou immédiatement après le premier démarrage. Hygie insère des valeurs par défaut vides au premier lancement ; l'outil de migration utilise `REPLACE INTO` pour les écraser avec les vraies valeurs SQLite.

---

### 🛠️ Développement

```bash
# Backend hot-reload + serveur Vite dev
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# Serveur Vite dev (terminal séparé)
cd frontend/vue && npm run dev
# Interface sur http://localhost:5173 (proxy vers :8000)
```

```bash
# Tests backend
pytest tests/ -q

# Vérifier les imports lm() (évite les pannes de scan)
python3 scripts/check_lm_imports.py

# Vérifier la cohérence i18n
python3 scripts/check_i18n.py
```

---

## License

MIT — see [LICENSE](LICENSE)
