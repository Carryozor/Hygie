<div align="center">

<img src="frontend/static/img/favicon-32.png" width="64" alt="Hygie">

# Hygie

**Smart media library manager for Emby, Jellyfin & Plex**
**Gestionnaire intelligent de bibliothèque média pour Emby, Jellyfin & Plex**

*Open-source alternative to Maintainerr*

[![Docker](https://img.shields.io/badge/Docker-ghcr.io%2Fcarryozor%2Fhygie-blue?logo=docker&logoColor=white)](https://github.com/carryozor/hygie/pkgs/container/hygie)
[![Version](https://img.shields.io/badge/version-3.0.1-brightgreen)](https://github.com/carryozor/hygie/releases)
[![CI](https://github.com/carryozor/hygie/actions/workflows/ci.yml/badge.svg)](https://github.com/carryozor/hygie/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-297%20passed-brightgreen)](https://github.com/carryozor/hygie/tree/main/tests)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python&logoColor=white)](https://www.python.org/)

---

🇬🇧 [English](#english) · 🇫🇷 [Français](#français)

</div>

---

## English

### What is Hygie?

Hygie automatically scans your **Emby, Jellyfin or Plex** media libraries, identifies unused media based on configurable rules, and orchestrates their deletion across your entire *arr stack — Emby/Jellyfin/Plex, Radarr, Sonarr, Seerr, and qBittorrent — while keeping your users informed via Discord.

### ✨ Features

#### 🖥️ Multi-Server Support (Emby, Jellyfin & Plex)
- **Automatic detection** of Emby and Jellyfin via API (`ProductName` + version heuristics)
- **Plex support** — scan libraries, detect unwatched media, delete via Plex local API
- **Plex.tv integration** — discover your servers and friends via `plex_tv_token`
- **Plex webhooks** — receive play/scrobble events at `/api/plex/webhook` (optional secret)
- **Multiple servers** — configure and activate several Emby/Jellyfin/Plex servers simultaneously
- Per-server enable/disable toggle with real-time type detection
- All operations (scan, delete, collection sync, overlay) routed to the correct server

#### 🎯 Deletion Rules
- **Per-library conditions** with AND/OR logic:
  - Added since X days
  - Not watched since X days
  - Never watched / play count
- **Configurable grace period** (minutes — set in Settings)
- **Simple rules** — per-user Seerr grace period overrides with optional Discord ID mapping
- **Expert rules** — visual condition builder with advanced fields (days not watched, rating, file size, media type, Seerr user…), AND/OR operators, queue or notify-only action
- **Seerr filters** — include or exclude specific users per library

#### 🔔 Discord Notifications
- Alerts at **30 days**, **7 days**, **24 hours** before deletion, and at deletion
- Automatic **@mention** of the requester (via Seerr mapping or manual Discord ID)
- Public TMDB poster — available even after media server deletion
- Notification sent **before** deletion so the image is still accessible

#### 🗑️ Orchestrated Deletion
Complete workflow in a single operation:
1. 📣 Discord notification (image still available)
2. 🗑️ Remove from **Emby / Jellyfin / Plex**
3. 🗑️ Remove item from **Radarr / Sonarr** (files preserved)
4. 🗑️ Delete request from **Seerr / Overseerr / Jellyseerr**
5. 🏷️ **qBittorrent** — add tag or delete torrent+files

#### 📺 "Leaving Soon" Collection
- Automatic sync of a collection with pending media
- **Poster overlay** showing "Deleted in Xd" countdown, refreshed on each sync
- Compatible with Emby and Jellyfin (Plex does not expose a collection write API)
- Overlay automatically removed if media is rescued from the queue

#### 🖥️ Web Interface (Vue 3 SPA)

| Page | Description |
|------|-------------|
| **Dashboard** | Global stats, lifetime deletions chart, upcoming deletions |
| **Calendar** | Deletions organized by date |
| **Queue** | Tabs: All / Pending / Deleted / Errors — persistent sort, bulk actions |
| **Libraries** | CRUD rules, clone, per-library scan |
| **Rules** | Visual builder — simple Seerr rules and expert condition rules |
| **Settings** | General (dry run, backup), Intervals, Plex token & webhook |
| **Ignored** | Excluded media with expiry, one-click restore to queue |
| **Storage** | Disk metrics from Radarr/Sonarr, reclaimable space |
| **Job History** | Scan and deletion checks with countdown to next run |
| **Logs** | Real-time stream via WebSocket, level/source filters |

#### 🎛️ Sidebar Controls
- **Dry Run toggle** — activate/deactivate simulation mode directly from the sidebar
- **Scan & deletion progress bars** — real-time countdown to next scheduled run with pulse animation when running

### 🚀 Quick Start

#### Docker Compose

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
      # Optional: encrypt API keys at rest.
      # Generate key: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
      # - HYGIE_ENCRYPTION_KEY=your-generated-key-here
    healthcheck:
      test: ["CMD", "python3", "/app/backend/healthcheck.py"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 20s
```

```bash
docker compose up -d
```

Open `http://localhost:8000`, create your admin account, then configure your services under **Settings**.

#### Prerequisites
- Docker & Docker Compose
- **Emby**, **Jellyfin**, or **Plex** media server
- Radarr and/or Sonarr
- Seerr / Overseerr / Jellyseerr *(optional but recommended)*
- qBittorrent *(optional)*
- Discord webhook *(optional)*

### ⚙️ Configuration

All configuration is done through the web interface.

| Service | Required fields |
|---------|----------------|
| **Media Server** | Internal URL, API key, External URL *(optional)* |
| **Radarr** | URL, API key |
| **Sonarr** | URL, API key |
| **Seerr** | URL, API key, External URL *(for clickable links)* |
| **qBittorrent** | URL *(Gluetun compatible)*, username, password |
| **Discord** | Webhook URL |
| **Plex** | `plex_tv_token` (Plex.tv token), webhook secret *(optional)* |

| Environment variable | Default | Description |
|---------------------|---------|-------------|
| `DB_PATH` | `/app/data/hygie.db` | SQLite database path |
| `DATABASE_URL` | *(unset)* | Set to `mysql+aiomysql://user:pass@host:3306/db` to use MariaDB instead of SQLite |
| `HYGIE_VERSION` | `dev` | Displayed version (injected via `--build-arg`) |
| `TZ` | UTC | Timezone |
| `HYGIE_ENCRYPTION_KEY` | *(unset)* | Fernet key for encrypting API keys at rest *(optional)* |

### 🔐 Encrypting API Keys at Rest

By default, service credentials are stored in plaintext in the SQLite database. You can enable **transparent at-rest encryption** by setting `HYGIE_ENCRYPTION_KEY`.

**1. Generate a key** (do this once, store it safely):

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# → qqUH3Oa9xK2mP8...  (copy this value)
```

**2. Store it in a `.env` file** (never commit this file):

```bash
# .env
HYGIE_ENCRYPTION_KEY=qqUH3Oa9xK2mP8...
```

Reference it in `docker-compose.yml`:

```yaml
    environment:
      - HYGIE_ENCRYPTION_KEY=${HYGIE_ENCRYPTION_KEY:-}
```

**3. Restart Hygie.** Existing credentials are automatically encrypted on first startup. No manual migration needed.

> **Important:** Back up your key. If lost, stored API keys cannot be decrypted and must be re-entered.

**Behavior without the key:** Hygie runs normally with plaintext storage — fully backward-compatible.

### 🔄 Deletion Workflow

```
Media Server Scan (Emby / Jellyfin / Plex)
    │
    ▼
Item matches conditions?
    │ Yes
    ▼
Added to queue (delete_at = now + grace_period)
    │
    ├──▶ Discord notification D-30
    ├──▶ Discord notification D-7
    ├──▶ Discord notification D-1
    │
    ▼  (delete_at reached)
1. 📣 Discord "Deleted"  ← image still on media server
2. 🗑️ Delete from Emby / Jellyfin / Plex
3. 🗑️ Delete from Radarr/Sonarr
4. 🗑️ Delete from Seerr
5. 🏷️ qBittorrent (tag or delete)
```

### 🛡️ Security

| Feature | Implementation |
|---------|---------------|
| Password hashing | **Argon2id** |
| Session tokens | **JWT HS256** with auto-generated 48-byte secret |
| Secret storage | `data/.secret` with `0600` permissions |
| Brute force protection | Rate limiting: 5 attempts / IP / 5 minutes (login & setup), persistent via SQLite |
| Image proxy | **SSRF protection** via host whitelist with asyncio.Lock |
| Dynamic SQL | **Safe column mapping** on all ORDER BY clauses |
| Input validation | **Pydantic** with min/max constraints |
| HTTP headers | `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy` |
| Container | **Non-root** user (UID 1000), **tini** as PID 1 |
| Database | **WAL mode**, integrity check in healthcheck |
| API keys at rest | Optional **Fernet AES-128** encryption via `HYGIE_ENCRYPTION_KEY` |
| Emby/Jellyfin auth | `X-Emby-Token` **header** — API key never in URL query string |
| Plex auth | `X-Plex-Token` **header** — token never in URL query string |
| WebSocket | Auth token required on first message (8KB size limit) |

> ⚠️ HTTPS strongly recommended via reverse proxy (Caddy, Traefik, Nginx)

### 🗄️ Database

Hygie supports **SQLite** (default) and **MariaDB**.

| Configuration | Recommended for |
|---------------|----------------|
| SQLite (default) | Personal use, < 200 000 media items, zero configuration |
| MariaDB | > 200 000 items, or existing MySQL/MariaDB server |

#### MariaDB via Docker Compose

```bash
DB_MARIADB_PASSWORD=my_password \
DATABASE_URL=mysql+aiomysql://hygie:my_password@mariadb:3306/hygie \
docker compose --profile mariadb up -d
```

#### SQLite → MariaDB migration

```bash
# Dry-run (no writes):
docker exec hygie python3 -m backend.tools.migrate_to_mariadb \
  --sqlite-path /app/data/hygie.db \
  --database-url "mysql+aiomysql://hygie:pass@mariadb:3306/hygie" \
  --dry-run

# Real migration (stop Hygie first):
docker exec hygie python3 -m backend.tools.migrate_to_mariadb \
  --sqlite-path /app/data/hygie.db \
  --database-url "mysql+aiomysql://hygie:pass@mariadb:3306/hygie"
```

### 🏗️ Build from Source

```bash
git clone https://github.com/carryozor/hygie.git
cd hygie

docker buildx build \
  --build-arg VERSION=3.0.0 \
  --platform linux/amd64 \
  -t ghcr.io/carryozor/hygie:latest \
  -t ghcr.io/carryozor/hygie:3.0.0 \
  --push .
```

### 🧱 Architecture

```
hygie/
├── backend/
│   ├── main.py                  # FastAPI app, lifespan, routers
│   ├── auth.py                  # JWT, Argon2id, SQLite-backed rate limiting
│   ├── scheduler.py             # APScheduler — main orchestration
│   ├── scanner.py               # Library scan logic (Emby/Jellyfin/Plex)
│   ├── deletion.py              # Deletion workflow (Emby/Jellyfin/Plex/arr)
│   ├── conditions.py            # Per-library condition evaluation
│   ├── notifications.py         # Discord notifications (configurable thresholds)
│   ├── overlay.py               # Pillow poster overlay generation
│   ├── collection.py            # "Leaving Soon" collection sync
│   ├── emby_client.py           # Emby & Jellyfin API client (multi-server)
│   ├── plex_client.py           # Plex local API client
│   ├── plex_tv_client.py        # Plex.tv cloud API client
│   ├── qbit_client.py           # qBittorrent (Gluetun + QUI proxy compatible)
│   ├── discord_client.py        # Discord webhooks
│   ├── healthcheck.py           # Docker healthcheck script
│   ├── db/
│   │   ├── engine.py            # DbConn abstraction (SQLite + MariaDB)
│   │   ├── schema.py            # Schema init, migrations, v2→v3 migration
│   │   ├── schema_mariadb.py    # MariaDB DDL
│   │   ├── repositories.py      # Expert rules CRUD
│   │   ├── settings_store.py    # Settings cache + defaults
│   │   ├── encryption.py        # Fernet helpers
│   │   └── utils.py             # Constants, pure helpers
│   ├── rules/
│   │   ├── models.py            # Pydantic models (ExpertRule, Condition…)
│   │   └── engine.py            # Expert rule evaluation engine
│   ├── arr_clients/             # Radarr, Sonarr, Seerr clients
│   ├── tools/
│   │   └── migrate_to_mariadb.py  # SQLite → MariaDB CLI migration
│   └── routers/                 # FastAPI routers (libraries, rules, plex, stats…)
└── frontend/
    └── vue/                     # Vue 3 + Vite 5 + Pinia SPA
        └── src/
            ├── views/           # Dashboard, Queue, Rules, Settings, Logs…
            ├── components/
            │   ├── rules/       # RuleTypeSelector, SimpleRuleForm, ExpertRuleBuilder…
            │   ├── media/       # MediaTable, MediaCard
            │   ├── layout/      # AppShell, Sidebar, TopBar
            │   └── ui/          # StatCard, ToggleSlider, HygieLogoSvg
            ├── stores/          # Pinia stores (auth, settings, servers, rules, stats)
            └── api/             # Axios client
```

**Stack:** FastAPI · APScheduler · aiosqlite / aiomysql · httpx · Pillow · PyJWT · cryptography · Python 3.12 · Vue 3 · Vite 5 · Pinia · TailwindCSS

### 🧪 Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

297 tests covering: database encryption, settings cache, auth (JWT/Argon2id/rate limiting), condition evaluation, expert rule engine, overlay rendering, Emby/Jellyfin client, Plex client (respx mocks), Plex webhook, scanner (Plex routing), deletion (Plex routing), MariaDB migration CLI, and FastAPI routes.

### 📋 Automatic Migrations

Hygie automatically migrates the database schema on startup. Upgrading from any previous version (including v1.x and v2.x) preserves all your data — encrypted credentials, library rules, queue history — with no manual intervention.

The v2 → v3 migration runs once and handles:
- Backfilling `server_id` and `deletion_unit` on pre-v3 library rows
- Converting `emby_url` / `emby_api_key` standalone settings into the `media_servers` JSON structure
- Migrating interval settings from hours to minutes

---

## Français

### Qu'est-ce que Hygie ?

Hygie scanne automatiquement vos bibliothèques **Emby, Jellyfin ou Plex**, identifie les médias inutilisés selon des règles configurables, et orchestre leur suppression sur toute votre stack *arr — Emby/Jellyfin/Plex, Radarr, Sonarr, Seerr et qBittorrent — tout en tenant vos utilisateurs informés via Discord.

### ✨ Fonctionnalités

#### 🖥️ Support Multi-Serveurs (Emby, Jellyfin & Plex)
- **Détection automatique** d'Emby et Jellyfin via l'API (`ProductName` + heuristique de version)
- **Support Plex** — scan des bibliothèques, détection des médias non-vus, suppression via l'API locale Plex
- **Intégration Plex.tv** — découverte de vos serveurs et amis via `plex_tv_token`
- **Webhooks Plex** — réception des événements lecture/scrobble sur `/api/plex/webhook` (secret optionnel)
- **Plusieurs serveurs** — configurez et activez plusieurs serveurs Emby/Jellyfin/Plex simultanément
- Toggle activer/désactiver par serveur avec détection de type en temps réel
- Toutes les opérations (scan, suppression, sync collection, overlay) routées vers le bon serveur

#### 🎯 Règles de suppression
- **Conditions par bibliothèque** avec logique ET/OU :
  - Ajouté depuis X jours
  - Non visionné depuis X jours
  - Jamais regardé / nombre de lectures
- **Délai de grâce configurable** (en minutes — réglable dans les Paramètres)
- **Règles simples** — délai de grâce personnalisé par utilisateur Seerr avec mapping Discord ID optionnel
- **Règles expertes** — constructeur visuel de conditions avancées (jours non-vu, note, taille, type de média, utilisateur Seerr…), opérateurs ET/OU, action file de suppression ou notification seule
- **Filtres Seerr** — inclure ou exclure certains utilisateurs par bibliothèque

#### 🔔 Notifications Discord
- Alertes à **30 jours**, **7 jours**, **24 heures** avant suppression, et à la suppression
- **Mention automatique** du demandeur (via mapping Seerr ou ID Discord manuel)
- Affiche TMDB publique — disponible même après suppression du serveur média
- Notification envoyée **avant** la suppression pour que l'image soit accessible

#### 🗑️ Suppression orchestrée
Workflow complet en une seule opération :
1. 📣 Notification Discord (image encore disponible)
2. 🗑️ Suppression dans **Emby / Jellyfin / Plex**
3. 🗑️ Suppression dans **Radarr / Sonarr** (fichiers conservés)
4. 🗑️ Suppression de la requête **Seerr / Overseerr / Jellyseerr**
5. 🏷️ **qBittorrent** — ajout d'un tag ou suppression torrent+fichier

#### 📺 Collection « Bientôt supprimé »
- Synchronisation automatique d'une collection avec les médias en attente
- **Overlay sur les affiches** affichant "Supprimé dans Xj", mis à jour à chaque sync
- Compatible Emby et Jellyfin (Plex n'expose pas d'API d'écriture de collections)
- Overlay automatiquement retiré si le média est sorti de la file

#### 🖥️ Interface web (Vue 3 SPA)

| Page | Description |
|------|-------------|
| **Tableau de bord** | Statistiques globales, graphe historique, prochaines suppressions |
| **Calendrier** | Suppressions organisées par date |
| **File d'attente** | Onglets Tous / En attente / Supprimés / Erreurs, tri persistant, actions groupées |
| **Bibliothèques** | CRUD des règles, clonage, scan individuel |
| **Règles** | Constructeur visuel — règles simples Seerr et règles expertes à conditions |
| **Paramètres** | Général (dry run, sauvegarde), Intervalles, Token Plex & webhook |
| **Ignorés** | Exclusions avec expiration, remise en file en un clic |
| **Stockage** | Métriques disque Radarr/Sonarr, espace récupérable |
| **Historique jobs** | Scans et vérifications avec décompte avant prochain lancement |
| **Logs** | Flux temps réel via WebSocket, filtres niveau/source |

#### 🎛️ Contrôles sidebar
- **Toggle Dry Run** — activer/désactiver le mode simulation directement depuis la barre latérale
- **Barres de progression** — décompte temps réel avant le prochain scan/vérification, avec animation de pulsation en cours d'exécution

### 🚀 Démarrage rapide

#### Docker Compose

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
      # Optionnel : chiffrer les clés API au repos.
      # Générer : python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
      # - HYGIE_ENCRYPTION_KEY=votre-clé-générée-ici
    healthcheck:
      test: ["CMD", "python3", "/app/backend/healthcheck.py"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 20s
```

```bash
docker compose up -d
```

Ouvre `http://localhost:8000`, crée ton compte administrateur, puis configure les services dans **Paramètres**.

#### Prérequis
- Docker & Docker Compose
- **Emby**, **Jellyfin**, ou **Plex** media server
- Radarr et/ou Sonarr
- Seerr / Overseerr / Jellyseerr *(optionnel mais recommandé)*
- qBittorrent *(optionnel)*
- Webhook Discord *(optionnel)*

### ⚙️ Configuration

Toute la configuration se fait depuis l'interface web.

| Service | Champs requis |
|---------|--------------|
| **Serveur Multimédia** | URL interne, clé API, URL externe *(optionnel)* |
| **Radarr** | URL, clé API |
| **Sonarr** | URL, clé API |
| **Seerr** | URL, clé API, URL externe *(pour les liens cliquables)* |
| **qBittorrent** | URL *(compatible Gluetun)*, utilisateur, mot de passe |
| **Discord** | URL du webhook |
| **Plex** | `plex_tv_token` (token Plex.tv), secret webhook *(optionnel)* |

| Variable d'environnement | Défaut | Description |
|--------------------------|--------|-------------|
| `DB_PATH` | `/app/data/hygie.db` | Chemin de la base SQLite |
| `DATABASE_URL` | *(absent)* | Mettre `mysql+aiomysql://user:pass@host:3306/db` pour utiliser MariaDB |
| `HYGIE_VERSION` | `dev` | Version affichée (injectée via `--build-arg`) |
| `TZ` | UTC | Fuseau horaire |
| `HYGIE_ENCRYPTION_KEY` | *(absent)* | Clé Fernet pour chiffrer les clés API en base *(optionnel)* |

### 🔐 Chiffrement des clés API au repos

Par défaut, les credentials sont stockés en clair dans la base SQLite. Vous pouvez activer un **chiffrement transparent au repos** via `HYGIE_ENCRYPTION_KEY`.

**1. Générer une clé** (une seule fois, à conserver précieusement) :

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# → qqUH3Oa9xK2mP8...  (copier cette valeur)
```

**2. La stocker dans un fichier `.env`** (ne jamais committer ce fichier) :

```bash
# .env
HYGIE_ENCRYPTION_KEY=qqUH3Oa9xK2mP8...
```

La référencer dans `docker-compose.yml` :

```yaml
    environment:
      - HYGIE_ENCRYPTION_KEY=${HYGIE_ENCRYPTION_KEY:-}
```

**3. Redémarrer Hygie.** Les credentials existants sont automatiquement chiffrés au premier démarrage.

> **Important :** Sauvegardez votre clé. En cas de perte, les clés API devront être re-saisies.

**Comportement sans la clé :** Hygie fonctionne normalement avec le stockage en clair.

### 🔄 Workflow de suppression

```
Scan Emby / Jellyfin / Plex
    │
    ▼
L'item remplit les conditions ?
    │ Oui
    ▼
Ajouté à la file (delete_at = maintenant + délai_de_grâce)
    │
    ├──▶ Notification Discord J-30
    ├──▶ Notification Discord J-7
    ├──▶ Notification Discord J-1
    │
    ▼  (delete_at atteint)
1. 📣 Discord « Supprimé »  ← image encore sur le serveur
2. 🗑️ Suppression Emby / Jellyfin / Plex
3. 🗑️ Suppression Radarr/Sonarr
4. 🗑️ Suppression Seerr
5. 🏷️ qBittorrent (tag ou suppression)
```

### 🛡️ Sécurité

| Fonctionnalité | Implémentation |
|----------------|---------------|
| Hashage des mots de passe | **Argon2id** |
| Tokens de session | **JWT HS256** avec secret auto-généré de 48 octets |
| Stockage du secret | `data/.secret` avec permissions `0600` |
| Protection brute force | Rate limiting : 5 tentatives / IP / 5 minutes, persistant via SQLite |
| Proxy d'images | **Protection SSRF** via whitelist d'hôtes avec asyncio.Lock |
| SQL dynamique | **Mapping de colonnes** sécurisé sur toutes les clauses ORDER BY |
| Validation des entrées | **Pydantic** avec contraintes min/max |
| En-têtes HTTP | `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy` |
| Conteneur | Utilisateur **non-root** (UID 1000), **tini** comme PID 1 |
| Base de données | **Mode WAL**, vérification d'intégrité dans le healthcheck |
| Clés API au repos | Chiffrement **Fernet AES-128** optionnel via `HYGIE_ENCRYPTION_KEY` |
| Auth Emby/Jellyfin | Header `X-Emby-Token` — clé API jamais dans l'URL |
| Auth Plex | Header `X-Plex-Token` — token jamais dans l'URL |
| WebSocket | Token auth sur le premier message (limite 8 Ko) |

> ⚠️ HTTPS fortement recommandé via reverse proxy (Caddy, Traefik, Nginx)

### 🗄️ Base de données

Hygie supporte **SQLite** (défaut) et **MariaDB**.

| Configuration | Recommandé pour |
|---------------|----------------|
| SQLite (défaut) | Usage personnel, < 200 000 médias, zéro configuration |
| MariaDB | > 200 000 médias, ou serveur MySQL/MariaDB déjà disponible |

#### MariaDB via Docker Compose

```bash
DB_MARIADB_PASSWORD=mon_mot_de_passe \
DATABASE_URL=mysql+aiomysql://hygie:mon_mot_de_passe@mariadb:3306/hygie \
docker compose --profile mariadb up -d
```

#### Migration SQLite → MariaDB

```bash
# Vérification sans écriture (dry-run) :
docker exec hygie python3 -m backend.tools.migrate_to_mariadb \
  --sqlite-path /app/data/hygie.db \
  --database-url "mysql+aiomysql://hygie:pass@mariadb:3306/hygie" \
  --dry-run

# Migration réelle (arrêter Hygie avant) :
docker exec hygie python3 -m backend.tools.migrate_to_mariadb \
  --sqlite-path /app/data/hygie.db \
  --database-url "mysql+aiomysql://hygie:pass@mariadb:3306/hygie"
```

### 🏗️ Build depuis les sources

```bash
git clone https://github.com/carryozor/hygie.git
cd hygie

docker buildx build \
  --build-arg VERSION=3.0.0 \
  --platform linux/amd64 \
  -t ghcr.io/carryozor/hygie:latest \
  -t ghcr.io/carryozor/hygie:3.0.0 \
  --push .
```

### 🧪 Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

297 tests couvrant : chiffrement de la base, cache des settings, authentification (JWT/Argon2id/rate limiting), évaluation des conditions, moteur de règles expertes, rendu des overlays, client Emby/Jellyfin, client Plex (mocks respx), webhook Plex, scanner (routage Plex), suppression (routage Plex), migration MariaDB, et routes FastAPI.

### 📋 Migrations automatiques

Hygie applique automatiquement les migrations de schéma au démarrage. La mise à jour depuis n'importe quelle version antérieure (y compris v1.x et v2.x) préserve toutes vos données — credentials chiffrés, règles de bibliothèques, historique — sans intervention manuelle.

La migration v2 → v3 s'exécute une seule fois et gère :
- Backfill de `server_id` et `deletion_unit` sur les lignes de bibliothèques pré-v3
- Conversion de `emby_url` / `emby_api_key` en structure `media_servers` JSON
- Migration des intervalles de heures en minutes

Voir [CHANGELOG.md](CHANGELOG.md) pour l'historique complet des versions.

---

<div align="center">

### ❤️ Support the project / Soutenir le projet

If Hygie is useful to you, consider supporting its development.
Si Hygie vous est utile, pensez à soutenir son développement.

[![PayPal](https://img.shields.io/badge/PayPal-Support%20Hygie-00457C?logo=paypal&logoColor=white)](https://paypal.me/AnThaumaturge)

---

[MIT License](LICENSE) © carryozor

</div>
