<div align="center">

<img src="frontend/static/img/logo.svg" width="96" alt="Hygie">

# Hygie

**Smart media library manager for Emby, Jellyfin & Plex**  
**Gestionnaire intelligent de bibliothèque média pour Emby, Jellyfin & Plex**

*Open-source alternative to Maintainerr*

[![Docker](https://img.shields.io/badge/Docker-ghcr.io%2Fcarryozor%2Fhygie-blue?logo=docker&logoColor=white)](https://github.com/carryozor/hygie/pkgs/container/hygie)
[![Version](https://img.shields.io/badge/version-3.4.2-brightgreen)](https://github.com/carryozor/hygie/releases)
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
- Real-time log stream via WebSocket
- Collapsible server/library tree in sidebar

#### 🎬 Multi-Server (Emby, Jellyfin & Plex)
- **Automatic type detection** via `/System/Info` (`ProductName` + version heuristics)
- **Plex support** — scan libraries, detect unwatched, delete via local API
- **Multiple servers** — configure several Emby/Jellyfin/Plex simultaneously
- Per-server color coding: Emby (green), Jellyfin (purple), Plex (orange)
- Auto-populated `serverId` for direct media links in the public calendar

#### 🎯 Expert Rules Engine
- **Visual condition builder** with AND/OR operators between condition groups
- Fields: days not watched, play count, community rating, file size (GB), added days ago, media type, Seerr user
- **Multi-library targeting** — one rule can cover libraries from multiple servers
- **Actions**: queue for deletion or notify-only
- Per-rule grace period override
- Simple Seerr rules: per-user grace periods with optional Discord ID mapping
- One-click scan trigger per rule, filtered to the rule's libraries only

#### 🔔 Discord Notifications
- Configurable threshold alerts (e.g., 7d, 1d before deletion) + at-deletion
- Automatic **@mention** of the requester (via Seerr mapping or Discord ID)
- Dual webhook support: main (deletions) + alerts (errors, failures)
- Separate alerts for: deletion failures, scan failures, Seerr unreachable
- Error threshold: batch alert when N consecutive failures occur

#### 🗑️ Orchestrated Deletion
Full deletion pipeline in sequence:
1. Discord notification (while media is still accessible for poster)
2. Emby / Jellyfin / Plex — remove item
3. Radarr / Sonarr — remove from library (files kept)
4. Overseerr / Jellyseerr — delete request
5. qBittorrent — tag or delete torrent (configurable)

#### 📅 Public Calendar
- Share upcoming deletions without requiring login
- Optional **password protection** and custom **URL slug** (`/myslug`)
- **Language selector** (8 languages, persists in localStorage)
- Filter by server, grouped by server → library
- **"View on Server"** link per media (Emby/Jellyfin with correct `serverId`)
- Hygie's configured language used as default

#### 🗄️ Database
- **SQLite** by default — zero configuration
- **MariaDB external** — set `DATABASE_URL` env var
- **MariaDB embedded** — single-container mode (`EMBEDDED_MARIADB=true`)
- Bidirectional migration UI: SQLite ↔ MariaDB
- Dialect-aware health check, rate limiting, backup, and VACUUM

#### 🛡️ Security
- Argon2id password hashing
- JWT HS256 with refresh tokens (auto-rotation)
- Rate limiting (SQLite-backed with in-memory fallback for MariaDB)
- SSRF-protected image proxy with domain whitelist
- Optional **Fernet encryption at rest** for sensitive settings (API keys, webhooks)
- Strict HTTP headers

#### 🔧 Operations
- APScheduler with countdown preservation across restarts
- Dry-run mode (global toggle from sidebar)
- **SQLite backup** with configurable interval and retention (SQLite only)
- Automatic VACUUM + WAL checkpoint after large purges (SQLite only)
- Structured logging with configurable level, WebSocket broadcast, retention
- Dedicated `/health` endpoint (dialect-aware) for Uptime Kuma / Docker HEALTHCHECK

---

### 🚀 Quick Start

#### Option 1 — SQLite (default, zero config)

```yaml
# docker-compose.yml
services:
  hygie:
    image: ghcr.io/carryozor/hygie:3.0.0
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
# Build with MariaDB support (~350 MB larger)
docker compose -f docker-compose.yml -f docker-compose.embedded-mariadb.yml build

# Start (runs as root to manage mysqld)
docker compose -f docker-compose.yml -f docker-compose.embedded-mariadb.yml up -d
```

#### Option 4 — Separate MariaDB container

```bash
docker compose --profile mariadb up -d
# Set DATABASE_URL=mysql+aiomysql://hygie:${DB_MARIADB_PASSWORD}@mariadb:3306/hygie
```

---

### ⚙️ Configuration

Copy `.env.example` to `.env`:

```bash
# Encryption key (recommended for production)
HYGIE_ENCRYPTION_KEY=<generate with: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">

# Database (leave empty for SQLite)
DATABASE_URL=

# MariaDB separate container
DB_MARIADB_PASSWORD=change_me
DB_MARIADB_ROOT_PASSWORD=change_me_root
```

All other settings are configurable from the **Settings** page in the UI.

---

### 📦 Stack Requirements

| Service | Required | Notes |
|---------|----------|-------|
| Emby / Jellyfin / Plex | ✅ | At least one media server |
| Radarr | Optional | Movie library management |
| Sonarr | Optional | TV library management |
| Overseerr / Jellyseerr | Optional | Request tracking, per-user rules |
| qBittorrent | Optional | Torrent tag or delete |
| Discord | Optional | Notifications |

---

### 🔄 Upgrading from v2.x

1. Back up `./data/hygie.db`
2. Update the image tag to `3.0.0`
3. The schema migration runs automatically at startup

**What changed since v2.8:**
- Frontend: complete Vue 3 rewrite (old vanilla JS removed)
- Database: SQLite/MariaDB abstraction, migration UI
- Rules: expert rules with multi-library support, Plex scanner integration
- Logs: translated in 8 languages
- Public calendar: language selector, server/library grouping, "View on Server" links
- Architecture: scheduler router, database router, media server type helpers

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

### 📊 Health Check

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "healthy",
  "version": "3.0.0",
  "dialect": "sqlite",
  "database": "sqlite / 15 tables",
  "scheduler": "4 jobs",
  "disk_free_mb": 45000,
  "disk": "ok",
  "encryption": "enabled"
}
```

---

## Français

### Qu'est-ce que Hygie ?

Hygie analyse automatiquement vos bibliothèques média **Emby, Jellyfin ou Plex**, identifie les médias inutilisés selon des règles configurables, et orchestre leur suppression sur toute votre stack *arr — Radarr, Sonarr, Overseerr/Jellyseerr, qBittorrent — en informant vos utilisateurs via Discord.

### ✨ Fonctionnalités

#### 🖥️ Interface — Vue 3
- Application Vue 3 + Vite moderne
- **8 langues** : français, anglais, allemand, espagnol, italien, portugais, néerlandais, polonais
- Flux de logs en temps réel via WebSocket
- Arbre serveur/bibliothèque rétractable dans la sidebar

#### 🎬 Multi-serveur (Emby, Jellyfin & Plex)
- **Détection automatique** du type via `/System/Info`
- **Support Plex** — scan bibliothèques, détection non-regardé, suppression via API locale
- **Plusieurs serveurs** — Emby/Jellyfin/Plex configurables simultanément
- Couleurs par type : Emby (vert), Jellyfin (violet), Plex (orange)
- `serverId` auto-peuplé pour les liens directs dans le calendrier public

#### 🎯 Moteur de règles expertes
- **Constructeur visuel** avec opérateurs AND/OR entre groupes de conditions
- Champs : jours sans visionnage, nombre de lectures, note communautaire, taille (Go), jours depuis ajout, type de média, utilisateur Seerr
- **Ciblage multi-bibliothèques** — une règle peut couvrir des bibliothèques de plusieurs serveurs
- **Actions** : mettre en file ou notifier seulement
- Délai de grâce configurable par règle
- Règles simples Seerr : délais par utilisateur avec Discord ID optionnel

#### 🔔 Notifications Discord
- Seuils configurables (ex. 7j, 1j avant suppression) + à la suppression
- **@mention** automatique du demandeur (via mapping Seerr ou Discord ID)
- Double webhook : principal (suppressions) + alertes (erreurs)
- Alertes dédiées : échecs suppression, échecs scan, Seerr inaccessible

#### 🗑️ Suppression orchestrée
Pipeline dans l'ordre :
1. Notification Discord (affiche encore accessible)
2. Emby / Jellyfin / Plex — suppression
3. Radarr / Sonarr — retrait (fichiers conservés)
4. Overseerr / Jellyseerr — suppression requête
5. qBittorrent — tag ou suppression torrent

#### 📅 Calendrier public
- Partagez les suppressions à venir sans login
- **Protection par mot de passe** et **slug URL personnalisé** (`/monslug`)
- **Sélecteur de langue** (8 langues, mémorisé)
- Filtre par serveur, groupé serveur → bibliothèque
- Lien **« Voir sur Serveur »** par média (avec `serverId` correct)

#### 🗄️ Base de données
- **SQLite** par défaut — zéro configuration
- **MariaDB externe** — variable `DATABASE_URL`
- **MariaDB embarquée** — mode tout-en-un (`EMBEDDED_MARIADB=true`)
- **Migration bidirectionnelle** SQLite ↔ MariaDB via l'interface
- Health check, rate limiting, backup et VACUUM dialect-aware

---

### 🚀 Démarrage rapide

#### Option 1 — SQLite (défaut, zéro config)

```yaml
services:
  hygie:
    image: ghcr.io/carryozor/hygie:3.0.0
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

---

### ⚙️ Configuration

Copier `.env.example` en `.env` :

```bash
# Clé de chiffrement (recommandée en production)
HYGIE_ENCRYPTION_KEY=<générer avec: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">

# Base de données (vide = SQLite)
DATABASE_URL=

# MariaDB conteneur séparé
DB_MARIADB_PASSWORD=changer_moi
DB_MARIADB_ROOT_PASSWORD=changer_moi_root
```

Tous les autres paramètres sont configurables depuis la page **Paramètres** de l'interface.

---

### 📦 Prérequis stack

| Service | Requis | Notes |
|---------|--------|-------|
| Emby / Jellyfin / Plex | ✅ | Au moins un serveur média |
| Radarr | Optionnel | Gestion bibliothèque films |
| Sonarr | Optionnel | Gestion bibliothèque séries |
| Overseerr / Jellyseerr | Optionnel | Suivi requêtes, règles par utilisateur |
| qBittorrent | Optionnel | Tag ou suppression torrent |
| Discord | Optionnel | Notifications |

---

### 🔄 Migration depuis v2.x

1. Sauvegarder `./data/hygie.db`
2. Mettre à jour le tag image vers `3.0.0`
3. La migration de schéma s'exécute automatiquement au démarrage

**Changements majeurs depuis v2.8 :**
- Interface : réécriture complète en Vue 3 (ancien JS vanilla supprimé)
- Base de données : abstraction SQLite/MariaDB, interface de migration
- Règles : règles expertes avec multi-bibliothèques, intégration scanner Plex
- Logs : traduits en 8 langues
- Calendrier public : sélecteur de langue, regroupement serveur/bibliothèque, liens « Voir sur Serveur »
- Architecture : router scheduler, router database, helpers type serveur

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

### 📊 Health Check

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "healthy",
  "version": "3.0.0",
  "dialect": "sqlite",
  "database": "sqlite / 15 tables",
  "scheduler": "4 jobs",
  "disk_free_mb": 45000,
  "disk": "ok",
  "encryption": "enabled"
}
```

---

## License

MIT — see [LICENSE](LICENSE)
