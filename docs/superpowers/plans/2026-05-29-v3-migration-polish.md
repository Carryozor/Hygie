# Hygie v3.0 — Phase 5: Migration v2→v3 + Polish + Release

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure users upgrading from v2.x to v3.0 experience a fully transparent and automatic migration — no manual steps, no data loss. Then finalize the release: update documentation, add Plex settings UI in Vue 3, and tag v3.0.0.

**Architecture:** The v2→v3 migration is handled entirely within the existing `init_db()` function (SQLite path) via additional migration steps that are idempotent. No separate migration script is needed — every `init_db()` call checks for v2 artifacts and upgrades them. The `_migrate_v2_to_v3()` function is added to `_init_db_sqlite()` as step 10. For MariaDB fresh installs, no migration is needed (users who already have v3 MariaDB installed started fresh). The UI gets a Plex configuration section in SettingsView.

**Tech Stack:** Python 3.12, FastAPI, Vue 3, SQLite (existing migration infrastructure)

**Prerequisite:** Phases 1–4 complete.

---

## File Structure

**Modified files:**
- `backend/db/schema.py` — add `_migrate_v2_to_v3()` migration step
- `tests/test_v2_to_v3_migration.py` — NEW migration tests
- `frontend/vue/src/views/SettingsView.vue` — add Plex configuration section
- `frontend/vue/src/views/SettingsView.vue` — add server management section
- `backend/version.py` — bump to `3.0.0`
- `README.md` — update upgrade guide, feature list
- `CHANGELOG.md` — create/update v3.0.0 section

---

### Task 1: Automatic v2→v3 database migration

The key v2→v3 schema changes that need migration:
1. `expert_rules.library_id` is INTEGER in v2.8 DDL but should remain as-is (no type change needed)
2. `media_queue` needs `plex_rating_key` and `view_count` columns (added in Phase 2)
3. Settings: `plex_webhook_secret` and `plex_tv_token` defaults (seeded by DEFAULT_SETTINGS)
4. `libraries.server_id` migration: v2 set server_id to `"0"` for all — this is already correct for v3
5. No data is lost: all v2 tables remain, all v2 data is preserved

The `_ensure_columns` mechanism (already in `init_db()`) handles adding new columns to existing tables automatically. The only extra step needed is verifying and logging the migration.

**Files:**
- Modify: `backend/db/schema.py`
- Create: `tests/test_v2_to_v3_migration.py`

- [ ] **Step 1: Write failing migration test**

```python
# tests/test_v2_to_v3_migration.py
"""Verify that a v2.8 SQLite database upgrades cleanly to v3.0."""
import asyncio
import os
import pytest
import aiosqlite

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")
os.environ.pop("DATABASE_URL", None)


async def _create_v28_db(path: str) -> None:
    """Bootstrap a minimal v2.8-compatible database."""
    async with aiosqlite.connect(path) as db:
        # v2.8 schema (subset — critical tables only)
        await db.execute("""
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)
        """)
        await db.execute("""
            CREATE TABLE media_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emby_id TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                media_type TEXT NOT NULL,
                library_id TEXT NOT NULL,
                library_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                poster_url TEXT DEFAULT '',
                detected_at TEXT NOT NULL,
                delete_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            )
        """)
        await db.execute("""
            CREATE TABLE expert_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                library_id INTEGER,
                conditions TEXT NOT NULL DEFAULT '[]',
                operator TEXT NOT NULL DEFAULT 'AND',
                action TEXT NOT NULL DEFAULT 'queue',
                enabled INTEGER NOT NULL DEFAULT 1,
                priority INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            )
        """)
        await db.execute("""
            CREATE TABLE libraries (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                emby_library_id TEXT NOT NULL,
                conditions TEXT NOT NULL DEFAULT '[]',
                logic TEXT NOT NULL DEFAULT 'AND',
                grace_days INTEGER NOT NULL DEFAULT 7,
                enabled INTEGER NOT NULL DEFAULT 1,
                server_id TEXT DEFAULT '0',
                deletion_unit TEXT NOT NULL DEFAULT 'episode'
            )
        """)
        # Seed some v2.8 data
        await db.execute(
            "INSERT INTO settings VALUES ('dry_run', 'false')"
        )
        await db.execute(
            "INSERT INTO expert_rules (name, conditions) VALUES ('Test Rule', '[{\"field\":\"jours_non_vu\",\"operator\":\">\",\"value\":365}]')"
        )
        await db.execute(
            "INSERT INTO libraries (id, name, emby_library_id) VALUES ('lib-001', 'Films', 'L1')"
        )
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, file_path, detected_at, delete_at) "
            "VALUES ('mq-001', 'Inception', 'movie', 'lib-001', 'Films', '/media/inception.mkv', '2025-01-01T00:00:00Z', '2025-07-01T00:00:00Z')"
        )
        await db.commit()


@pytest.mark.asyncio
async def test_v28_db_migrates_without_data_loss(tmp_path):
    """init_db() on a v2.8 DB adds new columns without deleting any data."""
    db_path = str(tmp_path / "v28.db")
    await _create_v28_db(db_path)

    import backend.db.engine as eng
    eng.SQLITE_PATH = db_path
    from backend.db.schema import init_db
    await init_db()

    async with aiosqlite.connect(db_path) as db:
        # v2.8 data preserved
        async with db.execute("SELECT name FROM expert_rules") as cur:
            rows = await cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "Test Rule"

        async with db.execute("SELECT name FROM libraries") as cur:
            lib = await cur.fetchone()
        assert lib and lib[0] == "Films"

        async with db.execute("SELECT title FROM media_queue") as cur:
            mq = await cur.fetchone()
        assert mq and mq[0] == "Inception"

        # New v3 columns exist in media_queue
        async with db.execute("PRAGMA table_info(media_queue)") as cur:
            cols = {r[1] for r in await cur.fetchall()}
        assert "plex_rating_key" in cols, "plex_rating_key column missing after migration"
        assert "view_count" in cols, "view_count column missing after migration"

        # New v3 settings seeded
        async with db.execute("SELECT value FROM settings WHERE key='plex_webhook_secret'") as cur:
            r = await cur.fetchone()
        assert r is not None, "plex_webhook_secret setting not seeded"


@pytest.mark.asyncio
async def test_v28_expert_rule_still_evaluable(tmp_path):
    """Expert rules stored in v2.8 format can still be evaluated by v3 engine."""
    from backend.rules.engine import evaluate_rule
    from backend.rules.models import ExpertRule, Condition, RuleOperator, RuleAction

    rule = ExpertRule(
        id=1, name="Old Rule", enabled=True, priority=0,
        operator=RuleOperator.AND, action=RuleAction.QUEUE,
        conditions=[Condition(field="jours_non_vu", operator=">", value=365)],
    )
    item = {"jours_non_vu": 400}
    assert evaluate_rule(rule, item) is True

    item2 = {"jours_non_vu": 100}
    assert evaluate_rule(rule, item2) is False


@pytest.mark.asyncio
async def test_init_db_idempotent_on_v3_db(tmp_path):
    """Running init_db() twice on a v3 DB does not corrupt data."""
    db_path = str(tmp_path / "v3.db")
    import backend.db.engine as eng
    eng.SQLITE_PATH = db_path
    from backend.db.schema import init_db

    await init_db()
    # Insert a record
    async with aiosqlite.connect(db_path) as db:
        await db.execute("INSERT INTO settings (key, value) VALUES ('test_key', 'test_val')")
        await db.commit()

    await init_db()  # Second run — must not fail or delete data

    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT value FROM settings WHERE key='test_key'") as cur:
            r = await cur.fetchone()
    assert r and r[0] == "test_val"
```

- [ ] **Step 2: Run to verify failure (plex columns missing)**

```bash
cd /opt/claude/hygie && python -m pytest tests/test_v2_to_v3_migration.py::test_v28_db_migrates_without_data_loss -v 2>&1 | tail -15
```
Expected: FAIL — `plex_rating_key column missing after migration`

- [ ] **Step 3: Add `_migrate_v2_to_v3()` to `backend/db/schema.py`**

The new columns (`plex_rating_key`, `view_count`) must be in the `_TABLES` migration list for `media_queue`. Verify they are already there from Phase 2 Task 4. If not, add them:

In `_TABLES`, find the `media_queue` entry and add to its migration list:
```python
("plex_rating_key", "TEXT DEFAULT ''"),
("view_count",      "INTEGER DEFAULT 0"),
```

Then add an explicit v2→v3 migration check at the end of `_init_db_sqlite()`, after all existing migration steps:

```python
        # Step 10: v2→v3 migration marker
        await _migrate_v2_to_v3(db)
        await db.commit()
```

And the function:
```python
async def _migrate_v2_to_v3(db) -> None:
    """One-time v2→v3 migration checks. All steps are idempotent."""
    # Check 1: media_queue has plex columns (handled by _ensure_columns above, just verify)
    cols = await _table_columns(db, "media_queue")
    if "plex_rating_key" not in cols:
        await db.execute("ALTER TABLE media_queue ADD COLUMN plex_rating_key TEXT DEFAULT ''")
        logger.info("v2→v3: added media_queue.plex_rating_key")
    if "view_count" not in cols:
        await db.execute("ALTER TABLE media_queue ADD COLUMN view_count INTEGER DEFAULT 0")
        logger.info("v2→v3: added media_queue.view_count")

    # Check 2: libraries has seerr_conditions column (v2.6 added it — just ensure it)
    if await _table_exists(db, "libraries"):
        lib_cols = await _table_columns(db, "libraries")
        if "seerr_conditions" not in lib_cols:
            await db.execute("ALTER TABLE libraries ADD COLUMN seerr_conditions TEXT NOT NULL DEFAULT '[]'")
            logger.info("v2→v3: added libraries.seerr_conditions")

    # Check 3: ensure notifications table exists (added in v2.8)
    # (handled by _TABLES CREATE TABLE IF NOT EXISTS — no action needed)

    # Log migration complete (only once per boot)
    logger.debug("v2→v3 migration checks complete")
```

- [ ] **Step 4: Run migration tests**

```bash
cd /opt/claude/hygie && python -m pytest tests/test_v2_to_v3_migration.py -v
```
Expected: 3 PASSED

- [ ] **Step 5: Run full test suite to verify no regression**

```bash
cd /opt/claude/hygie && python -m pytest tests/ -q 2>&1 | tail -5
```
Expected: all passing

- [ ] **Step 6: Commit**

```bash
git add backend/db/schema.py tests/test_v2_to_v3_migration.py
git commit -m "feat(migration): automatic v2→v3 DB migration via init_db() — transparent for users"
```

---

### Task 2: Plex settings section in Vue 3 SettingsView

**Files:**
- Modify: `frontend/vue/src/views/SettingsView.vue`

Add a "Plex" section after the existing settings sections. The section allows configuring:
- Plex local server URL and token (these are part of `media_servers` settings — open the server management panel)
- Plex.tv token (for shared users + server discovery)
- Plex webhook secret (optional — to secure the `/api/plex/webhook` endpoint)
- A "Tester la connexion" button that calls `GET /api/plex/test` (new endpoint — see below)

- [ ] **Step 1: Add `/api/plex/test` backend endpoint**

In `backend/routers/plex_webhook.py`, add:

```python
from ..auth import require_auth

@router.get("/test")
async def test_plex_connection(
    _user = Depends(require_auth),
) -> dict:
    """Test connectivity to configured Plex server(s)."""
    from ..db.settings_store import get_setting
    from ..db.media_servers import get_media_servers
    from ..plex_client import build_plex_client

    servers = await get_media_servers()
    plex_servers = [s for s in servers if s.get("type") == "plex"]

    results = []
    for srv in plex_servers:
        client = build_plex_client(srv)
        if not client:
            results.append({"server": srv.get("name", "?"), "ok": False, "error": "No URL/token"})
            continue
        try:
            libs = await client.get_libraries()
            results.append({"server": srv.get("name", "?"), "ok": True, "libraries": len(libs)})
        except Exception as e:
            results.append({"server": srv.get("name", "?"), "ok": False, "error": str(e)})

    # Test Plex.tv token
    plex_tv_token = get_setting("plex_tv_token") or ""
    tv_result = None
    if plex_tv_token:
        from ..plex_tv_client import PlexTVClient
        tv_ok = await PlexTVClient(plex_tv_token).validate_token()
        tv_result = {"ok": tv_ok}

    return {"servers": results, "plex_tv": tv_result}
```

- [ ] **Step 2: Add Plex section to `SettingsView.vue`**

After the existing "Général" and "Intervalles" sections, add:

```vue
<section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-5">
  <h2 class="font-semibold flex items-center gap-2">
    <span class="w-5 h-5 bg-orange-400 rounded-full flex-shrink-0" />
    Plex
  </h2>

  <div>
    <label class="block text-xs text-[var(--muted)] mb-1">Token Plex.tv (compte cloud)</label>
    <input v-model="form.plex_tv_token" type="password" placeholder="Votre token Plex.tv"
      class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:border-orange-400" />
    <p class="text-xs text-[var(--muted)] mt-1">Utilisé pour les amis Plex et la découverte de serveurs. Trouvable sur plex.tv/claim.</p>
  </div>

  <div>
    <label class="block text-xs text-[var(--muted)] mb-1">Secret webhook (optionnel)</label>
    <input v-model="form.plex_webhook_secret" type="text" placeholder="Laisser vide pour désactiver"
      class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:border-orange-400" />
    <p class="text-xs text-[var(--muted)] mt-1">
      URL webhook : <code class="bg-[var(--bg3)] px-1 rounded">{{ webhookUrl }}</code>
    </p>
  </div>

  <div class="flex items-center gap-3">
    <button @click="testPlex" :disabled="testingPlex"
      class="bg-orange-500/20 border border-orange-500/30 text-orange-400 hover:bg-orange-500/30 rounded-lg px-4 py-2 text-sm transition-colors disabled:opacity-50">
      {{ testingPlex ? 'Test en cours...' : 'Tester la connexion Plex' }}
    </button>
    <span v-if="plexTestResult" class="text-xs" :class="plexTestOk ? 'text-green-400' : 'text-red-400'">
      {{ plexTestResult }}
    </span>
  </div>
</section>
```

And in `<script setup>`:
```javascript
const testingPlex    = ref(false)
const plexTestResult = ref('')
const plexTestOk     = ref(false)

const webhookUrl = computed(() => {
  const secret = form.value.plex_webhook_secret
  return secret
    ? `${window.location.origin}/api/plex/webhook?secret=${secret}`
    : `${window.location.origin}/api/plex/webhook`
})

async function testPlex() {
  testingPlex.value = true
  plexTestResult.value = ''
  try {
    const { data } = await api.get('/plex/test')
    const ok = data.servers?.every(s => s.ok) ?? false
    plexTestOk.value = ok
    const serverCount = data.servers?.filter(s => s.ok).length || 0
    plexTestResult.value = ok
      ? `${serverCount} serveur(s) Plex connecté(s)`
      : `Erreur: ${data.servers?.find(s => !s.ok)?.error || 'inconnu'}`
  } catch {
    plexTestOk.value = false
    plexTestResult.value = 'Impossible de contacter le backend'
  } finally {
    testingPlex.value = false
  }
}
```

Also add `plex_tv_token` and `plex_webhook_secret` to `syncForm()` and `save()` in SettingsView:
```javascript
// In syncForm():
plex_tv_token:       settings.settings.plex_tv_token || '',
plex_webhook_secret: settings.settings.plex_webhook_secret || '',

// In save() payload:
plex_tv_token:       form.value.plex_tv_token,
plex_webhook_secret: form.value.plex_webhook_secret,
```

- [ ] **Step 3: Build and verify**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | tail -5
```
Expected: `✓ built`

- [ ] **Step 4: Commit**

```bash
cd /opt/claude/hygie && git add backend/routers/plex_webhook.py frontend/vue/src/views/SettingsView.vue
git commit -m "feat(plex): Plex settings section in Vue 3 UI + /api/plex/test endpoint"
```

---

### Task 3: README update + CHANGELOG + upgrade guide

**Files:**
- Modify: `README.md`
- Create/Modify: `CHANGELOG.md`

- [ ] **Step 1: Update README.md**

Add/replace the following sections:

```markdown
## Nouveautés v3.0

### Plex Media Server
- Support complet de Plex en plus d'Emby/Jellyfin
- Scan des bibliothèques, suppression, sessions actives
- Plex.tv : amis partagés, découverte de serveurs
- Webhooks temps réel (événements de lecture)

### Règles unifiées
- Deux types de règles : **Simple** (taux de visionnage, jours de grâce) et **Experte** (constructeur visuel)
- Glisser-déposer pour réordonner les conditions
- Récapitulatif logique en temps réel
- Coexistence des deux types dans une liste unifiée

### Interface Vue 3
- SPA Vue 3 + Vite (remplace le vanilla JS)
- Layout Library-Centric : sidebar par serveur, dashboard cross-serveur
- Logo triple arc SVG

### Base de données
- SQLite (défaut) ou MariaDB selon `DATABASE_URL`
- Script de migration SQLite→MariaDB inclus

## Mise à jour depuis v2.x

La migration v2→v3 est **automatique et transparente** :

```bash
docker pull ghcr.io/votre-org/hygie:3.0.0
docker compose up -d
```

Hygie 3.0 détecte automatiquement une base v2.x au démarrage et ajoute les nouvelles colonnes sans supprimer les données existantes.

**Aucune action manuelle requise.**

## Configuration Plex

1. Dans **Paramètres → Serveurs**, ajoutez un serveur de type `plex`
2. Renseignez l'URL locale (ex. `http://192.168.1.10:32400`) et votre token Plex
3. (Optionnel) Ajoutez votre token Plex.tv pour les amis partagés
4. (Optionnel) Configurez un secret webhook et ajoutez l'URL dans Plex → Paramètres → Webhooks
```

- [ ] **Step 2: Create `CHANGELOG.md`**

```markdown
# Changelog

## [3.0.0] — 2026-05-29

### Ajouté
- Support Plex Media Server (local API + Plex.tv cloud + webhooks)
- Système de règles unifié : Simple et Expert dans une même interface
- Constructeur visuel de règles expertes (glisser-déposer, récapitulatif logique)
- Interface Vue 3 + Vite (migration depuis vanilla JS)
- Layout Library-Centric (sidebar par serveur, dashboard cross-serveur)
- Logo triple arc SVG
- Support MariaDB via `DATABASE_URL` (SQLite reste le défaut)
- Script de migration SQLite→MariaDB (`python -m backend.tools.migrate_to_mariadb`)
- Endpoint webhook Plex (`POST /api/plex/webhook`)

### Migration v2→v3
- Automatique et transparente au démarrage
- Aucune action manuelle requise
- Toutes les données v2.x sont préservées

## [2.8.0] — 2026-05-24

### Ajouté
- Système de règles expertes avec conditions personnalisées
- Métriques par bibliothèque (`GET /api/metrics`)
- Table `notifications` avec contrainte UNIQUE (remplace colonnes `notified_*`)
- Tests d'intégration pour le scanner et la suppression

### Corrigé
- Isolation des DB dans les tests (patch des modules router)
- Bug de double file d'attente pour les règles expertes
```

- [ ] **Step 3: Commit**

```bash
cd /opt/claude/hygie && git add README.md CHANGELOG.md
git commit -m "docs: update README for v3.0 features + create CHANGELOG"
```

---

### Task 4: Final validation + tag v3.0.0

- [ ] **Step 1: Run complete test suite**

```bash
cd /opt/claude/hygie && python -m pytest tests/ -v 2>&1 | tail -20
```
Expected: all tests PASSED, 0 failures

- [ ] **Step 2: Build Vue production bundle**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | tail -5
```
Expected: `✓ built`

- [ ] **Step 3: Build and test Docker image**

```bash
cd /opt/claude/hygie && docker build -t hygie:3.0.0 .
docker run --rm -d -p 8003:8000 --name hygie-v3-final hygie:3.0.0
sleep 4

# Health check
curl -s http://localhost:8003/health | python3 -c "import sys,json; d=json.load(sys.stdin); print('OK' if d['status'] in ('healthy','degraded') else 'FAIL')"

# API version
curl -s http://localhost:8003/api/version | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['version'])"

# Frontend served
curl -s -o /dev/null -w "%{http_code}" http://localhost:8003/

docker stop hygie-v3-final
```
Expected:
```
OK
3.0.0
200
```

- [ ] **Step 4: Bump version to 3.0.0**

```python
# backend/version.py
VERSION = "3.0.0"
```

- [ ] **Step 5: Final commit + tag**

```bash
cd /opt/claude/hygie && git add backend/version.py
git commit -m "chore: release v3.0.0"
git tag v3.0.0
git push origin main --tags
```

- [ ] **Step 6: Push Docker image**

```bash
docker tag hygie:3.0.0 ghcr.io/$(git remote get-url origin | sed 's/.*github.com\///' | sed 's/\.git$//' | tr '[:upper:]' '[:lower:]')/hygie:3.0.0
docker tag hygie:3.0.0 ghcr.io/$(git remote get-url origin | sed 's/.*github.com\///' | sed 's/\.git$//' | tr '[:upper:]' '[:lower:]')/hygie:latest
# docker push ... (after `docker login ghcr.io`)
```
