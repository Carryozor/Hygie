<div align="center">

<img src="frontend/static/img/favicon-32.png" width="64" alt="Hygie">

# Hygie

**Smart media library manager for Emby & Jellyfin**
**Gestionnaire intelligent de bibliothèque média pour Emby & Jellyfin**

*Open-source alternative to Maintainerr*

[![Docker](https://img.shields.io/badge/Docker-ghcr.io%2Fcarryozor%2Fhygie-blue?logo=docker&logoColor=white)](https://github.com/carryozor/hygie/pkgs/container/hygie)
[![Version](https://img.shields.io/badge/version-2.5.1-brightgreen)](https://github.com/carryozor/hygie/releases)
[![CI](https://github.com/carryozor/hygie/actions/workflows/ci.yml/badge.svg)](https://github.com/carryozor/hygie/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-174%20passed-brightgreen)](https://github.com/carryozor/hygie/tree/main/tests)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python&logoColor=white)](https://www.python.org/)

---

🇬🇧 [English](#english) · 🇫🇷 [Français](#français)

</div>

---

## English

### What is Hygie?

Hygie automatically scans your **Emby or Jellyfin** media libraries, identifies unused media based on configurable rules, and orchestrates their deletion across your entire *arr stack — Emby/Jellyfin, Radarr, Sonarr, Seerr, and qBittorrent — while keeping your users informed via Discord.

### ✨ Features

#### 🖥️ Multi-Server Support (Emby & Jellyfin)
- **Automatic detection** of Emby and Jellyfin via API (`ProductName` + version heuristics)
- **Multiple servers** — configure and activate several Emby/Jellyfin servers simultaneously
- **Fusion icon** when both server types are active (split half-and-half design)
- Per-server enable/disable toggle with real-time type detection
- All operations (scan, delete, collection sync, overlay) routed to the correct server

#### 🎯 Deletion Rules
- **Per-library conditions** with AND/OR logic:
  - Added since X days
  - Not watched since X days
  - Never watched
  - Play count
- **Configurable grace period** (hours or minutes — selector in settings)
- **Per-user Seerr overrides** — different grace periods per requester
- **Seerr filters** — include or exclude specific users per library

#### 🔔 Discord Notifications
- Alerts at **30 days**, **7 days**, **24 hours** before deletion, and at deletion
- Automatic **@mention** of the requester (via Seerr mapping or manual Discord ID)
- Public TMDB poster — available even after media server deletion
- Notification sent **before** deletion so the image is still accessible

#### 🗑️ Orchestrated Deletion
Complete workflow in a single operation:
1. 📣 Discord notification (image still available)
2. 🗑️ Remove hardlink from **Emby / Jellyfin**
3. 🗑️ Remove item from **Radarr / Sonarr** (files preserved)
4. 🗑️ Delete request from **Seerr / Overseerr / Jellyseerr**
5. 🏷️ **qBittorrent** — add tag or delete torrent+files

#### 📺 "Leaving Soon" Collection
- Automatic sync of a collection with pending media
- **Poster overlay** showing "Deleted in Xd" countdown, refreshed on each sync
- Compatible with **both Emby and Jellyfin** (API confirmed identical)
- Overlay automatically removed if media is rescued from the queue

#### 🖥️ Web Interface

| Page | Description |
|------|-------------|
| **Dashboard** | Global stats, lifetime deletions chart, upcoming deletions |
| **Calendar** | Deletions organized by date |
| **Queue** | Tabs: All / Pending / Deleted / Errors — persistent sort, bulk actions |
| **Libraries** | CRUD rules, clone, per-library scan |
| **Settings** | Tabbed by service (Media Server, Radarr, Sonarr, Seerr, qBittorrent, Discord) |
| **Ignored** | Excluded media with expiry, one-click restore to queue |
| **Storage** | Disk metrics from Radarr/Sonarr, reclaimable space |
| **Job History** | Scan and deletion checks with countdown to next run |
| **Logs** | Real-time stream via WebSocket, level/source filters |

#### 🎛️ Sidebar Controls
- **Dry Run toggle** — activate/deactivate simulation mode directly from the sidebar without opening Settings
- **Scan & deletion progress bars** — real-time countdown to next scheduled run with pulse animation when running

#### 🌍 Internationalization
- **Auto-detection** from browser language
- French and English supported

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
- **Emby Media Server** or **Jellyfin** (auto-detected)
- Radarr and/or Sonarr
- Seerr / Overseerr / Jellyseerr *(optional but recommended)*
- qBittorrent *(optional)*
- Discord webhook *(optional)*

### ⚙️ Configuration

All configuration is done through the web interface. Settings are organized by service in horizontal tabs.

| Service | Required fields |
|---------|----------------|
| **Media Server** | Internal URL, API key, External URL *(optional)* |
| **Radarr** | URL, API key |
| **Sonarr** | URL, API key |
| **Seerr** | URL, API key, External URL *(for clickable links)* |
| **qBittorrent** | URL *(Gluetun compatible)*, username, password, QUI proxy URL *(optional)* |
| **Discord** | Webhook URL |

| Environment variable | Default | Description |
|---------------------|---------|-------------|
| `DB_PATH` | `/app/data/hygie.db` | SQLite database path |
| `HYGIE_VERSION` | `dev` | Displayed version (injected via `--build-arg`) |
| `TZ` | UTC | Timezone |
| `HYGIE_ENCRYPTION_KEY` | *(unset)* | Fernet key for encrypting API keys at rest *(optional — see below)* |

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
> A `.env.example` file is provided with generation instructions.

**Behavior without the key:** Hygie runs normally with plaintext storage — fully backward-compatible.

### 🔄 Deletion Workflow

```
Media Server Scan (Emby or Jellyfin)
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
2. 🗑️ Delete from Emby / Jellyfin
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
| Brute force protection | Rate limiting: 5 attempts / IP / 5 minutes (login & setup) |
| Image proxy | **SSRF protection** via host whitelist with asyncio.Lock |
| Dynamic SQL | **Safe column mapping** on all ORDER BY clauses |
| Input validation | **Pydantic** with min/max constraints |
| HTTP headers | `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy` |
| Container | **Non-root** user (UID 1000), **tini** as PID 1 |
| Database | **WAL mode**, integrity check in healthcheck |
| API keys at rest | Optional **Fernet AES-128** encryption via `HYGIE_ENCRYPTION_KEY` |
| Emby/Jellyfin auth | `X-Emby-Token` **header** — API key never in URL query string |
| WebSocket | Auth token required on first message (8KB size limit) |

> ⚠️ HTTPS strongly recommended via reverse proxy (Caddy, Traefik, Nginx)

### 🏗️ Build from Source

```bash
git clone https://github.com/carryozor/hygie.git
cd hygie

docker buildx build \
  --build-arg VERSION=2.5.1 \
  --platform linux/amd64 \
  -t ghcr.io/carryozor/hygie:latest \
  -t ghcr.io/carryozor/hygie:2.5.1 \
  --push .
```

### 🧱 Architecture

```
hygie/
├── backend/
│   ├── main.py              # FastAPI app, WebSocket, image proxy
│   ├── database.py          # SQLite, automatic migrations, encryption
│   ├── auth.py              # JWT, Argon2id, SQLite-backed rate limiting
│   ├── scheduler.py         # APScheduler — main orchestration
│   ├── conditions.py        # Per-library condition evaluation
│   ├── notifications.py     # Discord notifications (configurable thresholds)
│   ├── overlay.py           # Pillow poster overlay generation
│   ├── collection.py        # "Leaving Soon" collection sync
│   ├── emby_client.py       # Emby & Jellyfin API client (multi-server)
│   ├── arr_clients.py       # Radarr, Sonarr, Seerr clients
│   ├── qbit_client.py       # qBittorrent (Gluetun + QUI proxy compatible)
│   ├── discord_client.py    # Discord webhooks
│   ├── healthcheck.py       # Docker healthcheck script
│   └── routers/             # 11 FastAPI routers (incl. stats)
└── frontend/
    ├── templates/index.html  # Single Page App (no CDN)
    ├── static/css/hygie.css  # Unified CSS (extracted from template)
    └── static/js/            # 17 focused modules
        ├── api.js            # Fetch wrapper + Bearer auth
        ├── ui.js             # Shared utilities (toast, modals, proxyImg)
        ├── auth.js           # Authentication flow
        ├── nav.js            # Routing / page switching
        ├── dashboard.js      # Dashboard data
        ├── queue.js          # Media queue (sort, pagination, bulk)
        ├── libraries.js      # Library rules CRUD
        ├── settings.js       # Per-service settings
        ├── logs.js           # Log viewer
        ├── jobs.js           # Job history
        ├── ignored.js        # Ignored media + modal
        ├── websocket.js      # Real-time WebSocket client
        ├── calendar.js       # Calendar view
        ├── storage.js        # Storage metrics
        ├── unmonitored.js    # Unmonitored media
        ├── i18n.js           # FR/EN translations
        └── app.js            # Bootstrap + auto-refresh
```

**Stack:** FastAPI · APScheduler · SQLite (aiosqlite) · httpx · Pillow · PyJWT · cryptography · Python 3.12

### 🧪 Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

174 tests covering: database encryption, settings cache, auth (JWT/Argon2id/rate limiting), condition evaluation, overlay rendering, Emby client (header auth), and FastAPI routes (auth, proxy security, scheduler).

### 📋 Automatic Migrations

Hygie automatically migrates the database schema on startup. Upgrading from any previous version (including v1.x and v2.0) preserves all your data including encrypted credentials, library rules, and queue history.

---

## Français

### Qu'est-ce que Hygie ?

Hygie scanne automatiquement vos bibliothèques **Emby ou Jellyfin**, identifie les médias inutilisés selon des règles configurables, et orchestre leur suppression sur toute votre stack *arr — Emby/Jellyfin, Radarr, Sonarr, Seerr et qBittorrent — tout en tenant vos utilisateurs informés via Discord.

### ✨ Fonctionnalités

#### 🖥️ Support Multi-Serveurs (Emby & Jellyfin)
- **Détection automatique** d'Emby et Jellyfin via l'API (`ProductName` + heuristique de version)
- **Plusieurs serveurs** — configurez et activez plusieurs serveurs Emby/Jellyfin simultanément
- **Icône fusion** quand les deux types de serveurs sont actifs (split moitié-moitié)
- Toggle activer/désactiver par serveur avec détection de type en temps réel
- Toutes les opérations (scan, suppression, sync collection, overlay) routées vers le bon serveur

#### 🎯 Règles de suppression
- **Conditions par bibliothèque** avec logique ET/OU :
  - Ajouté depuis X jours
  - Non visionné depuis X jours
  - Jamais regardé
  - Nombre de lectures
- **Délai de grâce configurable** (en heures ou minutes — sélecteur dans les paramètres)
- **Règles Seerr par utilisateur** — délai personnalisé selon le demandeur
- **Filtres Seerr** — inclure ou exclure certains utilisateurs par bibliothèque

#### 🔔 Notifications Discord
- Alertes à **30 jours**, **7 jours**, **24 heures** avant suppression, et à la suppression
- **Mention automatique** du demandeur (via mapping Seerr ou ID Discord manuel)
- Affiche TMDB publique — disponible même après suppression du serveur média
- Notification envoyée **avant** la suppression pour que l'image soit accessible

#### 🗑️ Suppression orchestrée
Workflow complet en une seule opération :
1. 📣 Notification Discord (image encore disponible)
2. 🗑️ Retrait du hardlink **Emby / Jellyfin**
3. 🗑️ Suppression dans **Radarr / Sonarr** (fichiers conservés)
4. 🗑️ Suppression de la requête **Seerr / Overseerr / Jellyseerr**
5. 🏷️ **qBittorrent** — ajout d'un tag ou suppression torrent+fichier

#### 📺 Collection « Bientôt supprimé »
- Synchronisation automatique d'une collection avec les médias en attente
- **Overlay sur les affiches** affichant "Supprimé dans Xj", mis à jour à chaque sync
- Compatible **Emby et Jellyfin** (API confirmée identique)
- Overlay automatiquement retiré si le média est sorti de la file

#### 🖥️ Interface web

| Page | Description |
|------|-------------|
| **Tableau de bord** | Statistiques globales, graphe historique, prochaines suppressions |
| **Calendrier** | Suppressions organisées par date |
| **File d'attente** | Onglets Tous / En attente / Supprimés / Erreurs, tri persistant, actions groupées |
| **Bibliothèques** | CRUD des règles, clonage, scan individuel |
| **Paramètres** | Onglets par service (Serveur Multimédia, Radarr, Sonarr, Seerr, qBittorrent, Discord) |
| **Ignorés** | Exclusions avec expiration, remise en file en un clic |
| **Stockage** | Métriques disque Radarr/Sonarr, espace récupérable |
| **Historique jobs** | Scans et vérifications avec décompte avant prochain lancement |
| **Logs** | Flux temps réel via WebSocket, filtres niveau/source |

#### 🎛️ Contrôles sidebar
- **Toggle Dry Run** — activer/désactiver le mode simulation directement depuis la barre latérale sans ouvrir les Paramètres
- **Barres de progression** — décompte temps réel avant le prochain scan/vérification, avec animation de pulsation en cours d'exécution

#### 🌍 Internationalisation
- **Détection automatique** depuis la langue du navigateur
- Français et anglais supportés

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
- **Emby Media Server** ou **Jellyfin** (détection automatique)
- Radarr et/ou Sonarr
- Seerr / Overseerr / Jellyseerr *(optionnel mais recommandé)*
- qBittorrent *(optionnel)*
- Webhook Discord *(optionnel)*

### ⚙️ Configuration

Toute la configuration se fait depuis l'interface web, organisée en onglets par service.

| Service | Champs requis |
|---------|--------------|
| **Serveur Multimédia** | URL interne, clé API, URL externe *(optionnel)* |
| **Radarr** | URL, clé API |
| **Sonarr** | URL, clé API |
| **Seerr** | URL, clé API, URL externe *(pour les liens cliquables)* |
| **qBittorrent** | URL *(compatible Gluetun)*, utilisateur, mot de passe, URL proxy QUI *(optionnel)* |
| **Discord** | URL du webhook |

| Variable d'environnement | Défaut | Description |
|--------------------------|--------|-------------|
| `DB_PATH` | `/app/data/hygie.db` | Chemin de la base SQLite |
| `HYGIE_VERSION` | `dev` | Version affichée (injectée via `--build-arg`) |
| `TZ` | UTC | Fuseau horaire |
| `HYGIE_ENCRYPTION_KEY` | *(absent)* | Clé Fernet pour chiffrer les clés API en base *(optionnel — voir ci-dessous)* |

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
> Un fichier `.env.example` est fourni avec les instructions de génération.

**Comportement sans la clé :** Hygie fonctionne normalement avec le stockage en clair.

### 🔄 Workflow de suppression

```
Scan Emby / Jellyfin
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
2. 🗑️ Suppression Emby / Jellyfin
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
| Protection brute force | Rate limiting : 5 tentatives / IP / 5 minutes (login & setup) |
| Proxy d'images | **Protection SSRF** via whitelist d'hôtes avec asyncio.Lock |
| SQL dynamique | **Mapping de colonnes** sécurisé sur toutes les clauses ORDER BY |
| Validation des entrées | **Pydantic** avec contraintes min/max |
| En-têtes HTTP | `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy` |
| Conteneur | Utilisateur **non-root** (UID 1000), **tini** comme PID 1 |
| Base de données | **Mode WAL**, vérification d'intégrité dans le healthcheck |
| Clés API au repos | Chiffrement **Fernet AES-128** optionnel via `HYGIE_ENCRYPTION_KEY` |
| Auth Emby/Jellyfin | Header `X-Emby-Token` — clé API jamais dans l'URL |
| WebSocket | Token auth sur le premier message (limite 8 Ko) |

> ⚠️ HTTPS fortement recommandé via reverse proxy (Caddy, Traefik, Nginx)

### 🏗️ Build depuis les sources

```bash
git clone https://github.com/carryozor/hygie.git
cd hygie

docker buildx build \
  --build-arg VERSION=2.5.1 \
  --platform linux/amd64 \
  -t ghcr.io/carryozor/hygie:latest \
  -t ghcr.io/carryozor/hygie:2.5.1 \
  --push .
```

### 🧪 Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

174 tests couvrant : chiffrement de la base, cache des settings, authentification (JWT/Argon2id/rate limiting), évaluation des conditions, rendu des overlays, client Emby (auth par header) et routes FastAPI (auth, proxy SSRF, scheduler).

### 📋 Migrations automatiques

Hygie applique automatiquement les migrations de schéma au démarrage. La mise à jour depuis n'importe quelle version antérieure (y compris v1.x et v2.0) préserve toutes vos données, règles de bibliothèques et historique.

---

### 🆕 Changements v2.4.0 — Stabilité & sécurité

**Stabilité**
- Suppressions en parallèle avec `Semaphore(3)` — plus rapide, sans surcharge des services externes
- Retry HTTP avec backoff exponentiel (×3, 1s/2s/4s) sur TimeoutException et ConnectError
- Timeout 5s sur chaque envoi WebSocket — un client lent ne bloque plus les autres
- `init_db()` robuste avec base `:memory:` (tests et mode in-memory)

**Performance**
- 3 nouveaux index DB : `media_queue(emby_id)`, `media_queue(library_id)`, `ignored_media(emby_id)`
- Colonnes `notified_detected` et `notified_thresholds` intégrées au schéma principal (plus de migration lazy)

**Sécurité**
- Rate limiter : suppression du dead code, cleanup périodique toutes les 500 appels (mémoire bornée)
- `sanitize_url()` : les paramètres `api_key`, `token`, `password` sont masqués dans les logs
- Intervalles scheduler clampés à `[1, 10080]` — valeur 0 ou négative ne crashe plus APScheduler
- Validation mot de passe unifiée frontend/backend : minimum **8 caractères** partout

**Qualité de code**
- `_path_matches()` utilitaire commun pour la correspondance de chemins (Radarr/Sonarr)
- Logs Discord enrichis : les titres des médias non notifiés sont loggés en cas d'échec webhook
- Health check `/health` : détecte les jobs scheduler sans `next_run_time` (zombie jobs)
- `http_retry()` exporté depuis `database.py` — utilisable par tous les clients

**Notifications Discord v2.3.3** (intégrées dans cette version)
- Notification immédiate à la détection d'un média (avec date de suppression affichée)
- Paliers configurables dans l'onglet Discord des paramètres (ex: `7,1` pour j-7 et j-1)
- Titres dynamiques pour n'importe quel palier (`📅 Suppression dans 14 jours`, etc.)
- File d'attente colorée : Jaune `< 30j` · Orange `< 14j` · Rouge `< 7j` · Glow rouge animé `< 3j`

**Tests**
- 15 nouveaux tests (`test_v240_fixes.py`) : sanitize_url, http_retry, rate limiter, index DB

---

### 🆕 Changements v2.5.1 — CI, qualité & observabilité

**CI/CD**
- Pipeline GitHub Actions : tests Python 3.12 sur chaque push/PR, build & push Docker automatique sur tag `v*`

**Observabilité**
- Endpoint `/metrics` au format Prometheus (sans authentification — scraping direct par Prometheus/Grafana)
- Métriques exposées : `hygie_media_pending`, `hygie_media_deleted_total`, `hygie_media_ignored_total`, `hygie_media_errors`, `hygie_scans_total`, `hygie_deletion_checks_total`

**Nettoyage legacy**
- Suppression de `emby_url`/`emby_external_url` de la boucle whitelist proxy (déjà couverts par `media_servers`)
- Consolidation des deux blocs poster Emby en un seul — toutes les affiches passent désormais par le proxy backend
- Warning loggé si les settings legacy `emby_url`/`emby_external_url` sont encore utilisés

---

### 🆕 Changements v2.5.0 — Refactoring frontend & sécurité

**Frontend — Refactoring majeur**
- Suppression du CDN Tailwind JS (300 Ko inutiles au runtime — Hygie n'utilise que du CSS custom)
- CSS inline extrait vers `frontend/static/css/hygie.css` — template HTML allégé
- `app.js` monolithique découpé en **17 modules** ciblés : `api.js`, `ui.js`, `auth.js`, `nav.js`, `dashboard.js`, `queue.js`, `libraries.js`, `settings.js`, `logs.js`, `jobs.js`, `ignored.js`, `websocket.js`, `calendar.js`, `storage.js`, `unmonitored.js`, `i18n.js`, `app.js`
- Toutes les affiches (TMDB) routées via le proxy backend `/api/proxy/image` — résout les erreurs 407 derrière un proxy d'entreprise ou une VPN

**Backend — Modularisation**
- `scheduler.py` découpé en modules dédiés : `conditions.py`, `notifications.py`, `overlay.py`, `collection.py`
- Statistiques globales extraites vers `routers/stats.py`

**Sécurité**
- Rate limiting désormais **persistant via SQLite** — survie aux redémarrages du conteneur
- Avertissement affiché dans les Paramètres si `HYGIE_ENCRYPTION_KEY` n'est pas configuré
- Clés API **masquées dans l'UI** des Paramètres (seuls les 4 derniers caractères affichés)

**Tests**
- 174 tests (+ 2 tests rate limiting corrigés après migration SQLite)

---

<div align="center">

### ❤️ Support the project / Soutenir le projet

If Hygie is useful to you, consider supporting its development.
Si Hygie vous est utile, pensez à soutenir son développement.

[![PayPal](https://img.shields.io/badge/PayPal-Support%20Hygie-00457C?logo=paypal&logoColor=white)](https://paypal.me/AnThaumaturge)

---

[MIT License](LICENSE) © carryozor

</div>
