<div align="center">

<img src="frontend/static/img/favicon-32.png" width="64" alt="Hygie">

# Hygie

**Smart media library manager for Emby**
**Gestionnaire intelligent de bibliothèque média pour Emby**

*Open-source alternative to Maintainerr*

[![Docker](https://img.shields.io/badge/Docker-ghcr.io%2Fcarryozor%2Fhygie-blue?logo=docker&logoColor=white)](https://github.com/carryozor/hygie/pkgs/container/hygie)
[![Version](https://img.shields.io/badge/version-1.0.2-brightgreen)](https://github.com/carryozor/hygie/releases)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python&logoColor=white)](https://www.python.org/)

---

🇬🇧 [English](#english) · 🇫🇷 [Français](#français)

</div>

---

## English

### What is Hygie?

Hygie automatically scans your Emby libraries, identifies unused media based on configurable rules, and orchestrates their deletion across your entire *arr stack — Emby, Radarr, Sonarr, Seerr, and qBittorrent — while keeping your users informed via Discord.

### ✨ Features

#### 🎯 Deletion Rules
- **Per-library conditions** with AND/OR logic:
  - Added since X days
  - Not watched since X days
  - Never watched
  - Play count
- **Configurable grace period** (days between detection and deletion)
- **Per-user Seerr overrides** — different grace periods per requester
- **Seerr filters** — include or exclude specific users per library

#### 🔔 Discord Notifications
- Alerts at **30 days**, **7 days**, **24 hours** before deletion, and at deletion
- Automatic **@mention** of the requester (via Seerr mapping or manual Discord ID)
- Public TMDB poster — available even after Emby deletion
- Notification sent **before** Emby deletion so the image is still accessible

#### 🗑️ Orchestrated Deletion
Complete workflow in a single operation:
1. 📣 Discord notification (image still available)
2. 🗑️ Remove hardlink from **Emby**
3. 🗑️ Remove item from **Radarr / Sonarr** (files preserved)
4. 🗑️ Delete request from **Seerr / Overseerr / Jellyseerr**
5. 🏷️ **qBittorrent** — add tag or delete torrent+files

#### 📺 Emby "Leaving Soon" Collection
- Automatic sync of an Emby collection with pending media
- **Poster overlay** showing "Deleted in Xd" countdown, refreshed nightly
- Overlay automatically removed if media is rescued from the queue

#### 🖥️ Web Interface

| Page | Description |
|------|-------------|
| **Dashboard** | Global stats, lifetime deletions chart, upcoming deletions |
| **Calendar** | Deletions organized by date |
| **Queue** | Tabs: All / Pending / Deleted / Errors — persistent sort, bulk actions |
| **Libraries** | CRUD rules, clone, per-library scan |
| **Settings** | All service configuration, connection tests |
| **Ignored** | Excluded media with expiry, one-click restore to queue |
| **Storage** | Disk metrics from Radarr/Sonarr, reclaimable space |
| **Job History** | Scan and deletion checks with duration and status |
| **Logs** | Real-time stream via WebSocket, level/source filters |

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
```

```bash
docker compose up -d
```

Open `http://localhost:8000`, create your admin account, then configure your services under **Settings**.

#### Prerequisites
- Docker & Docker Compose
- Emby Media Server
- Radarr and/or Sonarr
- Seerr / Overseerr / Jellyseerr *(optional but recommended)*
- qBittorrent *(optional)*
- Discord webhook *(optional)*

### ⚙️ Configuration

All configuration is done through the web interface. No config files to edit.

| Service | Required fields |
|---------|----------------|
| **Emby** | Internal URL, API key, External URL *(for Discord posters)* |
| **Radarr** | URL, API key |
| **Sonarr** | URL, API key |
| **Seerr** | URL, API key, External URL *(for clickable links)* |
| **qBittorrent** | URL *(Gluetun compatible)*, username, password |
| **Discord** | Webhook URL |

| Environment variable | Default | Description |
|---------------------|---------|-------------|
| `DB_PATH` | `/app/data/hygie.db` | SQLite database path |
| `HYGIE_VERSION` | `dev` | Displayed version (injected via `--build-arg`) |
| `TZ` | UTC | Timezone |

### 🔄 Deletion Workflow

```
Emby Scan
    │
    ▼
Item matches conditions?
    │ Yes
    ▼
Added to queue (delete_at = now + grace_days)
    │
    ├──▶ Discord notification D-30
    ├──▶ Discord notification D-7
    ├──▶ Discord notification D-1
    │
    ▼  (delete_at reached)
1. 📣 Discord "Deleted"  ← image still on Emby
2. 🗑️ Delete from Emby
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
| Brute force protection | Rate limiting: 5 attempts / IP / 5 minutes |
| Image proxy | **SSRF protection** via host whitelist |
| Dynamic SQL | **Column/table whitelists** on all dynamic queries |
| Input validation | **Pydantic** with min/max length constraints |
| HTTP headers | `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy` |
| Container | **Non-root** user (UID 1000), **tini** as PID 1 |
| Database | **WAL mode**, integrity check in healthcheck |

> ⚠️ HTTPS strongly recommended via reverse proxy (Caddy, Traefik, Nginx)

### 🏗️ Build from Source

```bash
git clone https://github.com/carryozor/hygie.git
cd hygie

docker buildx build \
  --build-arg VERSION=1.0 \
  --platform linux/amd64 \
  -t ghcr.io/carryozor/hygie:latest \
  -t ghcr.io/carryozor/hygie:1.0 \
  --push .
```

### 🧱 Architecture

```
hygie/
├── backend/
│   ├── main.py              # FastAPI app, WebSocket, image proxy
│   ├── database.py          # SQLite, automatic migrations
│   ├── auth.py              # JWT, Argon2id, rate limiting
│   ├── scheduler.py         # Scan, deletions, notifications, overlay
│   ├── emby_client.py       # Emby API client
│   ├── arr_clients.py       # Radarr, Sonarr, Seerr clients
│   ├── qbit_client.py       # qBittorrent (Gluetun compatible)
│   ├── discord_client.py    # Discord webhooks
│   ├── healthcheck.py       # Docker healthcheck script
│   └── routers/             # 10 FastAPI routers
└── frontend/
    ├── templates/index.html  # Single Page App
    ├── static/js/app.js      # Vanilla JS
    └── static/js/i18n.js     # FR/EN translations
```

**Stack:** FastAPI · APScheduler · SQLite (aiosqlite) · httpx · Pillow · Python 3.12

### 📋 Automatic Migrations

Hygie automatically migrates the database schema on startup. Upgrading from any previous version preserves all your data.

---

## Français

### Qu'est-ce que Hygie ?

Hygie scanne automatiquement vos bibliothèques Emby, identifie les médias inutilisés selon des règles configurables, et orchestre leur suppression sur toute votre stack *arr — Emby, Radarr, Sonarr, Seerr et qBittorrent — tout en tenant vos utilisateurs informés via Discord.

### ✨ Fonctionnalités

#### 🎯 Règles de suppression
- **Conditions par bibliothèque** avec logique ET/OU :
  - Ajouté depuis X jours
  - Non visionné depuis X jours
  - Jamais regardé
  - Nombre de lectures
- **Délai de grâce configurable** (jours entre détection et suppression)
- **Règles Seerr par utilisateur** — délai personnalisé selon le demandeur
- **Filtres Seerr** — inclure ou exclure certains utilisateurs par bibliothèque

#### 🔔 Notifications Discord
- Alertes à **30 jours**, **7 jours**, **24 heures** avant suppression, et à la suppression
- **Mention automatique** du demandeur (via mapping Seerr ou ID Discord manuel)
- Affiche TMDB publique — disponible même après suppression d'Emby
- Notification envoyée **avant** la suppression Emby pour que l'image soit accessible

#### 🗑️ Suppression orchestrée
Workflow complet en une seule opération :
1. 📣 Notification Discord (image encore disponible)
2. 🗑️ Retrait du hardlink **Emby**
3. 🗑️ Suppression dans **Radarr / Sonarr** (fichiers conservés)
4. 🗑️ Suppression de la requête **Seerr / Overseerr / Jellyseerr**
5. 🏷️ **qBittorrent** — ajout d'un tag ou suppression torrent+fichier

#### 📺 Collection Emby « Bientôt supprimé »
- Synchronisation automatique d'une collection Emby avec les médias en attente
- **Overlay sur les affiches** affichant "Supprimé dans Xj", mis à jour chaque nuit
- Overlay automatiquement retiré si le média est sorti de la file

#### 🖥️ Interface web

| Page | Description |
|------|-------------|
| **Tableau de bord** | Statistiques globales, graphe historique, prochaines suppressions |
| **Calendrier** | Suppressions organisées par date |
| **File d'attente** | Onglets Tous / En attente / Supprimés / Erreurs, tri persistant, actions groupées |
| **Bibliothèques** | CRUD des règles, clonage, scan individuel |
| **Paramètres** | Configuration de tous les services, tests de connexion |
| **Ignorés** | Exclusions avec expiration, remise en file en un clic |
| **Stockage** | Métriques disque Radarr/Sonarr, espace récupérable |
| **Historique jobs** | Scans et vérifications avec durée et statut |
| **Logs** | Flux temps réel via WebSocket, filtres niveau/source |

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
```

```bash
docker compose up -d
```

Ouvre `http://localhost:8000`, crée ton compte administrateur, puis configure les services dans **Paramètres**.

#### Prérequis
- Docker & Docker Compose
- Emby Media Server
- Radarr et/ou Sonarr
- Seerr / Overseerr / Jellyseerr *(optionnel mais recommandé)*
- qBittorrent *(optionnel)*
- Webhook Discord *(optionnel)*

### ⚙️ Configuration

Toute la configuration se fait depuis l'interface web.

| Service | Champs requis |
|---------|--------------|
| **Emby** | URL interne, clé API, URL externe *(pour les affiches Discord)* |
| **Radarr** | URL, clé API |
| **Sonarr** | URL, clé API |
| **Seerr** | URL, clé API, URL externe *(pour les liens cliquables)* |
| **qBittorrent** | URL *(compatible Gluetun)*, utilisateur, mot de passe |
| **Discord** | URL du webhook |

| Variable d'environnement | Défaut | Description |
|--------------------------|--------|-------------|
| `DB_PATH` | `/app/data/hygie.db` | Chemin de la base SQLite |
| `HYGIE_VERSION` | `dev` | Version affichée (injectée via `--build-arg`) |
| `TZ` | UTC | Fuseau horaire |

### 🔄 Workflow de suppression

```
Scan Emby
    │
    ▼
L'item remplit les conditions ?
    │ Oui
    ▼
Ajouté à la file (delete_at = maintenant + grace_days)
    │
    ├──▶ Notification Discord J-30
    ├──▶ Notification Discord J-7
    ├──▶ Notification Discord J-1
    │
    ▼  (delete_at atteint)
1. 📣 Discord « Supprimé »  ← image encore sur Emby
2. 🗑️ Suppression Emby
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
| Protection brute force | Rate limiting : 5 tentatives / IP / 5 minutes |
| Proxy d'images | **Protection SSRF** via whitelist d'hôtes |
| SQL dynamique | **Whitelists** colonnes/tables sur toutes les requêtes dynamiques |
| Validation des entrées | **Pydantic** avec contraintes min/max de longueur |
| En-têtes HTTP | `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy` |
| Conteneur | Utilisateur **non-root** (UID 1000), **tini** comme PID 1 |
| Base de données | **Mode WAL**, vérification d'intégrité dans le healthcheck |

> ⚠️ HTTPS fortement recommandé via reverse proxy (Caddy, Traefik, Nginx)

### 🏗️ Build depuis les sources

```bash
git clone https://github.com/carryozor/hygie.git
cd hygie

docker buildx build \
  --build-arg VERSION=1.0 \
  --platform linux/amd64 \
  -t ghcr.io/carryozor/hygie:latest \
  -t ghcr.io/carryozor/hygie:1.0 \
  --push .
```

### 📋 Migrations automatiques

Hygie applique automatiquement les migrations de schéma au démarrage. La mise à jour depuis n'importe quelle version antérieure préserve toutes vos données.

---

<div align="center">

### ❤️ Support the project / Soutenir le projet

If Hygie is useful to you, consider supporting its development.
Si Hygie vous est utile, pensez à soutenir son développement.

[![PayPal](https://img.shields.io/badge/PayPal-Support%20Hygie-00457C?logo=paypal&logoColor=white)](https://paypal.me/AnThaumaturge)

---

[MIT License](LICENSE) © carryozor

</div>
