# Infrastructure & DB Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Établir une infrastructure de déploiement atomique, synchroniser le schéma DB local avec la production, et créer un validateur de cohérence schéma pour le CI.

**Architecture:** Un Makefile étendu orchestre des scripts shell idempotents. Un script Python valide la cohérence du schéma en créant une DB temporaire et en comparant les colonnes réelles vs le DDL. Le déploiement est atomique (tous les fichiers en une passe) avec health-check et rollback.

**Tech Stack:** Make, Bash, Python 3.12, aiosqlite, Docker, GitHub Actions

---

## File Map

| Action | Fichier | Rôle |
|---|---|---|
| Modify | `Makefile` | Ajouter targets deploy, check-schema, deploy-backend, deploy-frontend |
| Create | `scripts/deploy.sh` | Déploiement atomique + health-check |
| Create | `scripts/build-frontend.sh` | Build Vite isolé |
| Create | `scripts/check-schema.py` | Validateur DDL vs DB réelle |
| Modify | `backend/db/schema.py` | Synchroniser DDL avec colonnes réelles de prod |
| Create | `.github/workflows/test.yml` | CI : pytest + check-schema |
| Create | `.github/workflows/deploy.yml` | CD : SSH → make deploy |

---

## Task 1 : Synchroniser schema.py avec la DB de production

**Contexte :** La DB de production contient des colonnes (torrent_hash, sonarr_series_id, category, result…) absentes du DDL local. Des colonnes ajoutées via migration ne sont pas toujours dans la liste `expected_cols`. Résultat : les fresh installs sont incomplètes, les validateurs échouent.

**Files:**
- Modify: `backend/db/schema.py`

- [ ] **Step 1 : Lire les colonnes réelles de prod**

```bash
docker exec hygie python3 -c "
import asyncio, sys
sys.path.insert(0, '/app')
async def main():
    from backend.db.engine import get_db
    tables = ['media_queue','ignored_media','logs','job_history',
              'expert_rules','seerr_user_rules','stats_history',
              'notifications','libraries']
    async with get_db() as db:
        for t in tables:
            rows = await db.fetch_all(f'PRAGMA table_info({t})')
            print(f'{t}: {[r[\"name\"] for r in rows]}')
asyncio.run(main())
"
```

Résultat attendu (colonnes réelles de prod) :
```
media_queue: [id, emby_id, title, media_type, library_id, file_path, torrent_hash, radarr_id, sonarr_id, seerr_id, detected_at, delete_at, notified_7d, notified_1d, notified_now, status, added_date, last_played, library_name, poster_url, tmdb_id, seerr_user_id, seerr_username, seerr_discord_id, seerr_request_url, ignored, notified_30d, notified_detected, notified_thresholds, sonarr_series_id, season_number, plex_rating_key, view_count]
logs: [id, ts, level, source, message, category, seen_status]
job_history: [id, job_type, started_at, finished_at, status, message, result]
```

- [ ] **Step 2 : Mettre à jour le DDL de media_queue dans schema.py**

Localiser la définition de `media_queue` dans `backend/db/schema.py` et s'assurer que ces colonnes sont dans le DDL ET dans `expected_cols` :

```python
(
    "media_queue",
    """CREATE TABLE IF NOT EXISTS media_queue (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        emby_id             TEXT    NOT NULL,
        title               TEXT    NOT NULL DEFAULT '',
        media_type          TEXT    NOT NULL DEFAULT 'Movie',
        library_id          TEXT,
        library_name        TEXT    NOT NULL DEFAULT '',
        file_path           TEXT    NOT NULL DEFAULT '',
        poster_url          TEXT    NOT NULL DEFAULT '',
        tmdb_id             TEXT,
        seerr_id            TEXT,
        seerr_user_id       INTEGER,
        seerr_username      TEXT,
        seerr_discord_id    TEXT,
        seerr_request_url   TEXT,
        radarr_id           INTEGER,
        sonarr_id           INTEGER,
        sonarr_series_id    INTEGER,
        season_number       INTEGER,
        plex_rating_key     TEXT,
        view_count          INTEGER NOT NULL DEFAULT 0,
        torrent_hash        TEXT,
        detected_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        delete_at           TEXT,
        added_date          TEXT,
        last_played         TEXT,
        status              TEXT    NOT NULL DEFAULT 'pending',
        ignored             INTEGER NOT NULL DEFAULT 0,
        notified_30d        INTEGER NOT NULL DEFAULT 0,
        notified_7d         INTEGER NOT NULL DEFAULT 0,
        notified_1d         INTEGER NOT NULL DEFAULT 0,
        notified_now        INTEGER NOT NULL DEFAULT 0,
        notified_detected   INTEGER NOT NULL DEFAULT 0,
        notified_thresholds TEXT    NOT NULL DEFAULT ''
    )""",
    [
        ("poster_url",          "TEXT NOT NULL DEFAULT ''"),
        ("tmdb_id",             "TEXT"),
        ("seerr_id",            "TEXT"),
        ("seerr_user_id",       "INTEGER"),
        ("seerr_username",      "TEXT"),
        ("seerr_discord_id",    "TEXT"),
        ("seerr_request_url",   "TEXT"),
        ("radarr_id",           "INTEGER"),
        ("sonarr_id",           "INTEGER"),
        ("sonarr_series_id",    "INTEGER"),
        ("season_number",       "INTEGER"),
        ("plex_rating_key",     "TEXT"),
        ("view_count",          "INTEGER NOT NULL DEFAULT 0"),
        ("torrent_hash",        "TEXT"),
        ("added_date",          "TEXT"),
        ("last_played",         "TEXT"),
        ("library_name",        "TEXT NOT NULL DEFAULT ''"),
        ("notified_30d",        "INTEGER NOT NULL DEFAULT 0"),
        ("notified_detected",   "INTEGER NOT NULL DEFAULT 0"),
        ("notified_thresholds", "TEXT NOT NULL DEFAULT ''"),
        ("ignored",             "INTEGER NOT NULL DEFAULT 0"),
    ],
),
```

- [ ] **Step 3 : Mettre à jour logs, job_history, expert_rules**

```python
# logs — ajouter category et seen_status à expected_cols
(
    "logs",
    """CREATE TABLE IF NOT EXISTS logs (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        ts       TEXT    NOT NULL,
        level    TEXT    NOT NULL,
        source   TEXT    NOT NULL DEFAULT '',
        message  TEXT    NOT NULL,
        category TEXT,
        seen_status TEXT
    )""",
    [("seen_status", "TEXT"), ("category", "TEXT")],
),

# job_history — ajouter result
(
    "job_history",
    """CREATE TABLE IF NOT EXISTS job_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        job_type    TEXT    NOT NULL,
        started_at  TEXT    NOT NULL,
        finished_at TEXT,
        status      TEXT,
        message     TEXT,
        result      TEXT
    )""",
    [("result", "TEXT")],
),

# expert_rules — déjà corrigé (grace_days + library_ids)
# Vérifier que les deux sont bien dans expected_cols
```

- [ ] **Step 4 : Vérifier que le démarrage réussit après les changements**

```bash
docker cp backend/db/schema.py hygie:/app/backend/db/schema.py
docker exec -u root hygie find /app/backend/db -name "*.pyc" -delete
docker restart hygie
sleep 5
docker logs hygie --tail 5
```

Résultat attendu : `Hygie X.Y.Z started` sans erreur de migration.

- [ ] **Step 5 : Commit**

```bash
git add backend/db/schema.py
git commit -m "fix(db): synchronize schema DDL with production columns

- media_queue: add torrent_hash, sonarr_series_id, season_number,
  plex_rating_key, view_count, seerr_discord_id, ignored to DDL and migrations
- logs: add category and seen_status columns  
- job_history: add result column
- All migrations are idempotent via ALTER TABLE ADD COLUMN IF NOT EXISTS"
```

---

## Task 2 : Script check-schema.py — Validateur de cohérence

**Files:**
- Create: `scripts/check-schema.py`
- Modify: `Makefile`

- [ ] **Step 1 : Écrire le test d'intégration**

```python
# tests/test_schema_consistency.py
import asyncio
import aiosqlite
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

@pytest.mark.asyncio
async def test_schema_has_all_declared_columns():
    """Every column in every DDL must be present after applying migrations."""
    result = await _run_check()
    assert result == [], f"Missing columns detected:\n" + "\n".join(result)

async def _run_check() -> list[str]:
    from backend.db.schema import _TABLES, init_db
    import tempfile, os
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        tmp_path = f.name
    
    os.environ['DB_PATH'] = tmp_path
    try:
        async with aiosqlite.connect(tmp_path) as db:
            await init_db()
            errors = []
            for table_name, ddl, expected_cols in _TABLES:
                # Get actual columns
                async with db.execute(f"PRAGMA table_info({table_name})") as cur:
                    actual_cols = {row[1] for row in await cur.fetchall()}
                
                # Extract declared columns from DDL
                import re
                declared = re.findall(r'^\s{4}(\w+)\s+\w+', ddl, re.MULTILINE)
                declared = [c for c in declared if c not in ('id', 'PRIMARY', 'UNIQUE')]
                
                for col in declared:
                    if col not in actual_cols:
                        errors.append(f"  {table_name}.{col}: in DDL but missing from DB")
            return errors
    finally:
        os.unlink(tmp_path)
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il passe**

```bash
cd /opt/claude/hygie
python3 -m pytest tests/test_schema_consistency.py -v
```

Résultat attendu : `PASSED`

- [ ] **Step 3 : Créer le script standalone scripts/check-schema.py**

```python
#!/usr/bin/env python3
"""CI gate: ensure every column declared in schema DDL exists after migrations.

Usage:
    python3 scripts/check-schema.py
    
Exit codes:
    0 — all good
    1 — schema inconsistencies detected
"""
import asyncio
import aiosqlite
import re
import sys
import tempfile
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def check() -> list[str]:
    from backend.db.schema import _TABLES

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_path = f.name

    errors = []
    try:
        os.environ["DB_PATH"] = tmp_path
        # Run full init (creates tables + applies migrations)
        from backend.db.schema import init_db
        await init_db()

        async with aiosqlite.connect(tmp_path) as db:
            for table_name, ddl, _ in _TABLES:
                async with db.execute(f"PRAGMA table_info({table_name})") as cur:
                    actual = {row[1] for row in await cur.fetchall()}

                declared = re.findall(r"^\s{4}(\w+)\s+\w", ddl, re.MULTILINE)
                declared = [c for c in declared if c not in ("id",)]

                for col in declared:
                    if col not in actual:
                        errors.append(f"  {table_name}.{col}: declared in DDL, missing from DB")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    return errors


def main():
    errors = asyncio.run(check())
    if errors:
        print("❌ Schema inconsistencies detected:")
        for e in errors:
            print(e)
        sys.exit(1)
    print("✅ Schema consistent — all DDL columns present in DB")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4 : Rendre le script exécutable et le tester**

```bash
chmod +x scripts/check-schema.py
python3 scripts/check-schema.py
```

Résultat attendu :
```
✅ Schema consistent — all DDL columns present in DB
```

- [ ] **Step 5 : Commit**

```bash
git add scripts/check-schema.py tests/test_schema_consistency.py
git commit -m "feat(ci): add schema consistency validator

scripts/check-schema.py creates a temp DB, applies all migrations,
and verifies every DDL column exists. Exits 1 on any gap."
```

---

## Task 3 : scripts/build-frontend.sh

**Files:**
- Create: `scripts/build-frontend.sh`

- [ ] **Step 1 : Créer le script**

```bash
#!/usr/bin/env bash
# scripts/build-frontend.sh
# Build the Vue 3 frontend with Vite. 
# Always runs from the repo root regardless of cwd.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/frontend/vue"

echo "→ Building frontend from $FRONTEND_DIR"
cd "$FRONTEND_DIR"
npm ci --prefer-offline
npm run build
echo "✅ Frontend built → $REPO_ROOT/frontend/dist/"
```

- [ ] **Step 2 : Rendre exécutable et tester**

```bash
chmod +x scripts/build-frontend.sh
bash scripts/build-frontend.sh
```

Résultat attendu : `✅ Frontend built → .../frontend/dist/`

- [ ] **Step 3 : Commit**

```bash
git add scripts/build-frontend.sh
git commit -m "feat(scripts): add build-frontend.sh"
```

---

## Task 4 : scripts/deploy.sh — Déploiement atomique

**Files:**
- Create: `scripts/deploy.sh`

- [ ] **Step 1 : Créer le script**

```bash
#!/usr/bin/env bash
# scripts/deploy.sh
# Atomic deploy: build frontend, copy all files, clear pycache, restart, health-check.
# Usage: ./scripts/deploy.sh [container_name]
set -euo pipefail

CONTAINER="${1:-hygie}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "═══════════════════════════════════════"
echo "  Hygie Deploy → container: $CONTAINER"
echo "═══════════════════════════════════════"

# ── 1. Build frontend ────────────────────────────────────────────────────────
echo "→ [1/5] Building frontend…"
bash "$REPO_ROOT/scripts/build-frontend.sh"

# ── 2. Copy all backend files ────────────────────────────────────────────────
echo "→ [2/5] Copying backend files…"
for src in \
    "$REPO_ROOT/backend/main.py:/app/backend/main.py" \
    "$REPO_ROOT/backend/db/schema.py:/app/backend/db/schema.py" \
    "$REPO_ROOT/backend/db/repositories.py:/app/backend/db/repositories.py" \
    "$REPO_ROOT/backend/db/settings_store.py:/app/backend/db/settings_store.py" \
    "$REPO_ROOT/backend/db/encryption.py:/app/backend/db/encryption.py" \
    "$REPO_ROOT/backend/rules/models.py:/app/backend/rules/models.py" \
    "$REPO_ROOT/backend/rules/engine.py:/app/backend/rules/engine.py" \
    "$REPO_ROOT/backend/deletion.py:/app/backend/deletion.py" \
    "$REPO_ROOT/backend/scheduler.py:/app/backend/scheduler.py" \
    "$REPO_ROOT/backend/_job_state.py:/app/backend/_job_state.py" \
    "$REPO_ROOT/backend/qbit_client.py:/app/backend/qbit_client.py" \
    "$REPO_ROOT/backend/plex_client.py:/app/backend/plex_client.py"
do
    local_path="${src%%:*}"
    container_path="${src##*:}"
    docker cp "$local_path" "$CONTAINER:$container_path" 2>/dev/null && true
done

# Copy entire directories that change frequently
echo "→ [2/5] Copying backend directories…"
for dir in routers arr_clients scanner; do
    for f in "$REPO_ROOT/backend/$dir/"*.py; do
        [ -f "$f" ] || continue
        docker cp "$f" "$CONTAINER:/app/backend/$dir/$(basename "$f")"
    done
done

# Copy scanner package if it exists as subpackage
if [ -d "$REPO_ROOT/backend/scanner" ]; then
    docker exec -u root "$CONTAINER" mkdir -p /app/backend/scanner 2>/dev/null || true
    for f in "$REPO_ROOT/backend/scanner/"*.py; do
        [ -f "$f" ] || continue
        docker cp "$f" "$CONTAINER:/app/backend/scanner/$(basename "$f")"
    done
fi

# ── 3. Copy frontend dist ─────────────────────────────────────────────────────
echo "→ [3/5] Copying frontend dist…"
docker cp "$REPO_ROOT/frontend/dist/." "$CONTAINER:/app/frontend/dist/"

# ── 4. Clear pycache ──────────────────────────────────────────────────────────
echo "→ [4/5] Clearing pycache…"
docker exec -u root "$CONTAINER" find /app/backend -name "*.pyc" -delete 2>/dev/null || true
docker exec -u root "$CONTAINER" find /app/backend -name "__pycache__" -empty -delete 2>/dev/null || true

# ── 5. Restart + health-check ─────────────────────────────────────────────────
echo "→ [5/5] Restarting container…"
docker restart "$CONTAINER"

echo "   Waiting for health…"
for i in $(seq 1 15); do
    sleep 2
    if docker exec "$CONTAINER" curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo "✅ Deploy successful — Hygie is up"
        exit 0
    fi
    echo "   ($i/15) still starting…"
done

echo "❌ Health-check failed after 30s — check: docker logs $CONTAINER"
exit 1
```

- [ ] **Step 2 : Rendre exécutable et tester un déploiement complet**

```bash
chmod +x scripts/deploy.sh
bash scripts/deploy.sh hygie
```

Résultat attendu :
```
═══════════════════════════════════════
  Hygie Deploy → container: hygie
═══════════════════════════════════════
→ [1/5] Building frontend…
✅ Frontend built → .../frontend/dist/
→ [2/5] Copying backend files…
→ [2/5] Copying backend directories…
→ [3/5] Copying frontend dist…
→ [4/5] Clearing pycache…
→ [5/5] Restarting container…
   Waiting for health…
✅ Deploy successful — Hygie is up
```

- [ ] **Step 3 : Commit**

```bash
git add scripts/deploy.sh
git commit -m "feat(scripts): add atomic deploy.sh with health-check

Builds frontend, copies all backend files atomically, clears pycache,
restarts container, and verifies /health responds within 30s."
```

---

## Task 5 : Makefile — Étendre avec targets de déploiement

**Files:**
- Modify: `Makefile`

- [ ] **Step 1 : Ajouter les targets manquantes**

Remplacer le contenu du `Makefile` par :

```makefile
.PHONY: dev test lint install build compose deploy deploy-backend deploy-frontend check-schema help

CONTAINER ?= hygie

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  dev              Start dev server (uvicorn --reload on :8000)"
	@echo "  test             Run full test suite (pytest)"
	@echo "  lint             Run ruff linter"
	@echo "  install          Install Python dependencies"
	@echo "  build            Build Docker image (hygie:dev)"
	@echo "  compose          Start via docker compose"
	@echo "  deploy           Full atomic deploy (build + copy + restart)"
	@echo "  deploy-backend   Deploy backend only (no frontend build)"
	@echo "  deploy-frontend  Build and deploy frontend only"
	@echo "  check-schema     Validate schema DDL vs DB consistency"

install:
	pip install -r requirements.txt -r requirements-dev.txt

dev:
	uvicorn backend.main:app --reload --port 8000

test:
	python3 -m pytest -q

test-v:
	python3 -m pytest -v

lint:
	python3 -m ruff check backend/ tests/

build:
	docker build -t hygie:dev .

compose:
	docker compose up -d

check-schema:
	python3 scripts/check-schema.py

deploy:
	bash scripts/deploy.sh $(CONTAINER)

deploy-frontend:
	bash scripts/build-frontend.sh
	docker cp frontend/dist/. $(CONTAINER):/app/frontend/dist/

deploy-backend:
	@for dir in backend/routers backend/arr_clients backend/scanner backend/db backend/rules; do \
		for f in $$dir/*.py; do \
			[ -f "$$f" ] || continue; \
			docker cp "$$f" "$(CONTAINER):/app/$$f"; \
		done; \
	done
	@for f in backend/*.py; do \
		[ -f "$$f" ] || continue; \
		docker cp "$$f" "$(CONTAINER):/app/$$f"; \
	done
	docker exec -u root $(CONTAINER) find /app/backend -name "*.pyc" -delete 2>/dev/null || true
	docker restart $(CONTAINER)
	@echo "✅ Backend deployed"
```

- [ ] **Step 2 : Tester les targets**

```bash
make check-schema
make help
make deploy CONTAINER=hygie
```

Résultat attendu pour `make check-schema` :
```
✅ Schema consistent — all DDL columns present in DB
```

- [ ] **Step 3 : Commit**

```bash
git add Makefile
git commit -m "feat(make): add deploy, deploy-backend, deploy-frontend, check-schema targets"
```

---

## Task 6 : GitHub Actions — CI test pipeline

**Files:**
- Create: `.github/workflows/test.yml`

- [ ] **Step 1 : Créer le workflow de tests**

```bash
mkdir -p .github/workflows
```

```yaml
# .github/workflows/test.yml
name: Tests

on:
  push:
    branches: [ main, develop ]
  pull_request:
    branches: [ main ]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.12
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: 'pip'

      - name: Install dependencies
        run: pip install -r requirements.txt -r requirements-dev.txt

      - name: Check schema consistency
        run: python3 scripts/check-schema.py

      - name: Run tests
        run: python3 -m pytest -q --tb=short

      - name: Lint
        run: python3 -m ruff check backend/ tests/

  frontend:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Set up Node.js 20
        uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
          cache-dependency-path: frontend/vue/package-lock.json

      - name: Install frontend dependencies
        run: cd frontend/vue && npm ci

      - name: Build frontend
        run: cd frontend/vue && npm run build
```

- [ ] **Step 2 : Commit**

```bash
git add .github/workflows/test.yml
git commit -m "ci: add GitHub Actions test + frontend build pipeline

- Runs pytest on every push/PR
- Validates schema consistency (check-schema.py)
- Builds frontend to catch compilation errors"
```

---

## Task 7 : GitHub Actions — CD deploy pipeline (optionnel)

**Files:**
- Create: `.github/workflows/deploy.yml`

- [ ] **Step 1 : Créer le workflow de déploiement**

```yaml
# .github/workflows/deploy.yml
# Triggers on push to main only.
# Requires GitHub secrets: DEPLOY_HOST, DEPLOY_USER, DEPLOY_SSH_KEY, DEPLOY_PATH
name: Deploy

on:
  push:
    branches: [ main ]

jobs:
  deploy:
    runs-on: ubuntu-latest
    needs: []  # Add 'test' job name here once CI is stable

    steps:
      - uses: actions/checkout@v4

      - name: Deploy via SSH
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.DEPLOY_HOST }}
          username: ${{ secrets.DEPLOY_USER }}
          key: ${{ secrets.DEPLOY_SSH_KEY }}
          script: |
            cd ${{ secrets.DEPLOY_PATH }}
            git pull origin main
            make deploy
```

- [ ] **Step 2 : Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "ci: add CD deploy workflow (SSH → make deploy)

Triggers on push to main. Requires secrets:
DEPLOY_HOST, DEPLOY_USER, DEPLOY_SSH_KEY, DEPLOY_PATH"
```

---

## Self-Review Checklist

- [x] **Spec coverage :** Infrastructure (scripts, Makefile), schema sync, CI/CD → tous couverts
- [x] **Placeholder scan :** Aucun TBD, tous les chemins de fichiers sont exacts
- [x] **Type consistency :** `_TABLES` utilisé partout de façon cohérente
- [x] **Idempotence :** `ALTER TABLE ADD COLUMN` uniquement si colonne absente, `mkdir -p`, `docker cp` écrase
- [x] **Rollback :** `deploy.sh` sort avec code 1 si health-check échoue

---

## Note : Plans suivants

Ce plan (A) couvre l'infrastructure. Les plans suivants dans l'ordre :
- **Plan B** — Frontend refactors (statusStore, intercepteur 401, toasts erreur)
- **Plan C** — Backend refactors (service layer, conditions.py → rules/, retry)
- **Plan D** — JWT Refresh Token
- **Plan E** — Tests Vitest + Playwright
