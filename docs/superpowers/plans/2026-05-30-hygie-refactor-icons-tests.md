# Hygie — Refactor, Icons & Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all engineer/architect findings from the code review, add service brand icons, auto-detect Emby vs Jellyfin, and establish a test foundation.

**Architecture:** Incremental improvements — no breaking interface changes. Icon component uses `simple-icons` npm package (SVG path data only, tree-shaken at build). Auto-detect calls the backend test endpoint on URL debounce. Backend fixes are isolated to single files. Tests use pytest-asyncio with a tmp-file SQLite DB.

**Tech Stack:** Vue 3 + Vite 5 + TailwindCSS (frontend); FastAPI + aiosqlite (backend); pytest + pytest-asyncio (tests); simple-icons (brand SVGs)

**Deployment:** Frontend → `npm run build` then `docker cp dist/. hygie:/app/frontend/dist/ && docker restart hygie`. Backend files → `docker cp file hygie:/app/backend/... && docker restart hygie`.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `frontend/vue/src/components/ui/ServiceIcon.vue` | Create | Brand SVG icons for all integrated services |
| `frontend/vue/src/views/SettingsView.vue` | Modify | Use ServiceIcon; auto-detect server type on URL change |
| `frontend/vue/package.json` | Modify | Add `simple-icons` dependency |
| `backend/db/schema.py` | Modify | Remove duplicate `if not rows: return 0` guard |
| `backend/emby_client.py` | Modify | Move local imports to module top |
| `backend/emby_client.py` | Modify | Fix `Limit: 100000` → paginated fetch |
| `backend/main.py` | Modify | Extract health endpoint to `routers/health.py` |
| `backend/routers/health.py` | Create | Standalone health router |
| `backend/db/schema.py` | Modify | MariaDB migration parity — call `_migrate_libraries_to_expert_rules` via DbConn |
| `backend/scanner.py` | Modify | Extract `_build_expert_queue_entry` helper |
| `backend/conditions.py` | Modify | Add `ScanContext` dataclass; update `_evaluate_item` signature |
| `backend/tests/__init__.py` | Create | Test package |
| `backend/tests/test_rules_engine.py` | Create | Tests for `rules/engine.py` |
| `backend/tests/test_conditions.py` | Create | Tests for `conditions.py` evaluation logic |

---

## Task 1: Install simple-icons + Create ServiceIcon.vue

**Files:**
- Modify: `frontend/vue/package.json`
- Create: `frontend/vue/src/components/ui/ServiceIcon.vue`

- [ ] **Step 1: Add simple-icons to package.json**

Edit `frontend/vue/package.json` — add to `dependencies`:

```json
{
  "dependencies": {
    "axios": "^1.7.2",
    "pinia": "^2.1.7",
    "simple-icons": "^13.0.0",
    "vue": "^3.4.29",
    "vue-router": "^4.3.3",
    "@vueuse/core": "^10.11.0"
  }
}
```

- [ ] **Step 2: Install the package**

```bash
cd /opt/claude/hygie/frontend/vue && npm install
```

Expected: `added X packages` with no errors.

- [ ] **Step 3: Create ServiceIcon.vue**

```vue
<!-- frontend/vue/src/components/ui/ServiceIcon.vue -->
<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    :fill="iconColor"
    xmlns="http://www.w3.org/2000/svg"
    :aria-label="name"
    role="img"
  >
    <path :d="iconPath" />
  </svg>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 20 },
  color: { type: String, default: null },
})

// SVG paths sourced from simple-icons (https://simpleicons.org) where available,
// custom paths for services not in the registry.
const ICONS = {
  // ── Media servers ──────────────────────────────────────────────────────────
  emby: {
    path: 'M3.579 10.958L0 9.217v5.565l3.579-1.74V10.958zm.83 2.09v-1.01L7.35 10.5v.97L4.408 13.05zm3.773-.58v-1.013l2.94-1.447v1.01L8.182 12.47zm3.76-.58V10.88l2.95-1.447v1.012L11.94 11.89zM17.34 9.217L13.76 10.958v2.083l3.58 1.741V9.217zm-.83 3.832l-2.94-1.447v-.97l2.94 1.447v.97zm1.66-1.213V10.82l2.97-1.543v5.565l-2.97-1.46v-.346zm2.97-3.76L17.34 9.217 12 6.565 6.66 9.217 3.579 10.958 0 9.217 12 3l12 6.217-2.86 1.74v-.88z',
    hex: '52B54B',
  },
  jellyfin: {
    path: 'M12 0C5.372 0 0 5.372 0 12s5.372 12 12 12 12-5.372 12-12S18.628 0 12 0zm0 2.4c5.302 0 9.6 4.298 9.6 9.6s-4.298 9.6-9.6 9.6S2.4 17.302 2.4 12 6.698 2.4 12 2.4zM8.4 6l-1.2 2.4H6v1.2h.6L5.4 12h2.4L9 9.6h1.2L9 12h2.4l1.2-2.4h.6V8.4h-1.2L10.8 6H8.4zm5.4 0l-1.2 2.4h1.2l1.2 2.4h2.4l-1.2-2.4h.6V7.2h-1.2L15.6 6h-1.8z',
    hex: '00A4DC',
  },
  plex: {
    path: 'M11.994 0C5.367 0 0 5.367 0 11.994 0 18.623 5.367 24 11.994 24 18.623 24 24 18.623 24 11.994 24 5.367 18.623 0 11.994 0zm4.403 13.034l-6.498 4.46V6.496l6.498 4.52-.001.018z',
    hex: 'E5A00D',
  },
  // ── Arr apps ───────────────────────────────────────────────────────────────
  radarr: {
    path: 'M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm0 2c5.523 0 10 4.477 10 10s-4.477 10-10 10S2 17.523 2 12 6.477 2 12 2zm0 2a8 8 0 100 16A8 8 0 0012 4zm0 2a6 6 0 110 12A6 6 0 0112 6zm0 2a4 4 0 100 8 4 4 0 000-8zm0 2a2 2 0 110 4 2 2 0 010-4z',
    hex: 'FFC230',
  },
  sonarr: {
    path: 'M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm1.313 5.004l4.306 7.028-4.318 7.051H9.406l4.307-7.038-4.32-7.041h3.92z',
    hex: '35C5F4',
  },
  overseerr: {
    path: 'M12 2C6.477 2 2 6.477 2 12s4.477 10 10 10 10-4.477 10-10S17.523 2 12 2zm0 2c4.418 0 8 3.582 8 8 0 4.418-3.582 8-8 8-4.418 0-8-3.582-8-8 0-4.418 3.582-8 8-8zm-1 3v5H7l5 5 5-5h-4V7h-2z',
    hex: 'F5A623',
  },
  seerr: {
    path: 'M12 2C6.477 2 2 6.477 2 12s4.477 10 10 10 10-4.477 10-10S17.523 2 12 2zm0 2c4.418 0 8 3.582 8 8 0 4.418-3.582 8-8 8-4.418 0-8-3.582-8-8 0-4.418 3.582-8 8-8zm-1 3v5H7l5 5 5-5h-4V7h-2z',
    hex: 'F5A623',
  },
  // ── Communication ──────────────────────────────────────────────────────────
  discord: {
    path: 'M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057c.002.022.015.045.033.06a19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028c.462-.63.874-1.295 1.226-1.994a.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z',
    hex: '5865F2',
  },
  // ── Torrent clients ────────────────────────────────────────────────────────
  qbittorrent: {
    path: 'M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm0 1.846c5.595 0 10.154 4.56 10.154 10.154 0 5.595-4.559 10.154-10.154 10.154C6.405 22.154 1.846 17.595 1.846 12 1.846 6.405 6.405 1.846 12 1.846zm-.577 2.77v7.115H8.077L12 17.308l3.923-5.577h-3.346V4.616h-1.154z',
    hex: '1EAEFF',
  },
  qui: {
    path: 'M12 2a10 10 0 1 0 0 20A10 10 0 0 0 12 2zm0 2a8 8 0 1 1 0 16A8 8 0 0 1 12 4zm0 2a6 6 0 1 0 4.243 10.243l1.414 1.414 1.414-1.414-1.414-1.414A6 6 0 0 0 12 6zm0 2a4 4 0 1 1 0 8 4 4 0 0 1 0-8zm2.828 5.172L16.243 14.6a4 4 0 0 1-.415.443l-1.414-1.414c.159-.14.306-.294.414-.457z',
    hex: '7B5EA7',
  },
}

const DEFAULT = {
  path: 'M12 2a10 10 0 1 0 0 20A10 10 0 0 0 12 2z',
  hex: '6B7280',
}

const iconData = computed(() => ICONS[props.name?.toLowerCase()] || DEFAULT)
const iconPath = computed(() => iconData.value.path)
const iconColor = computed(() => props.color || `#${iconData.value.hex}`)
</script>
```

- [ ] **Step 4: Verify component renders**

Build and confirm no import errors:
```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | tail -5
```

Expected: build succeeds with no errors.

---

## Task 2: Update SettingsView.vue — Service Icons + Auto-Detect

**Files:**
- Modify: `frontend/vue/src/views/SettingsView.vue`

The current server icons use Font Awesome generic icons (`fa-server`, `fa-play`). Replace with `ServiceIcon` and add auto-detect on URL change.

- [ ] **Step 1: Import ServiceIcon in SettingsView.vue**

At line 484 (`<script setup>`), add the import after existing imports:

```js
import ServiceIcon from '@/components/ui/ServiceIcon.vue'
```

- [ ] **Step 2: Update SERVER_CONFIG to use service names**

Replace the current `SERVER_CONFIG` block (lines 533–543):

```js
// Map server type → ServiceIcon name + border/header classes
const SERVER_CONFIG = {
  emby:     { service: 'emby',     border: 'border-green-600/30',  header: 'bg-green-600/10' },
  jellyfin: { service: 'jellyfin', border: 'border-blue-500/30',   header: 'bg-blue-500/10' },
  plex:     { service: 'plex',     border: 'border-yellow-500/30', header: 'bg-yellow-500/10' },
}
const DEF = { service: null, border: 'border-[var(--border)]', header: '' }

function serverService(type)     { return (SERVER_CONFIG[type] || DEF).service }
function serverBorderClass(type) { return (SERVER_CONFIG[type] || DEF).border }
function serverHeaderClass(type) { return (SERVER_CONFIG[type] || DEF).header }
```

- [ ] **Step 3: Update server header template to use ServiceIcon**

Replace the icon section in the server header (the `<div class="w-9 h-9 ...">` block around line 161):

```html
<!-- Server type logo -->
<div class="w-9 h-9 rounded-lg flex items-center justify-center bg-black/20">
  <ServiceIcon v-if="serverService(srv.type)" :name="serverService(srv.type)" :size="22" />
  <i v-else class="fas fa-server text-[var(--muted)] text-sm" />
</div>
```

- [ ] **Step 4: Add auto-detect watcher**

After the `testServer` function (around line 558), add a debounced URL watcher per server. Since servers is a ref array, we watch deep and detect URL changes by diffing:

```js
// Auto-detect server type (Emby vs Jellyfin) when URL changes on a saved server
const _detectTimers = new Map()

function scheduleAutoDetect(srv) {
  if (!srv.id) return  // only test saved servers
  if (_detectTimers.has(srv._uid)) clearTimeout(_detectTimers.get(srv._uid))
  _detectTimers.set(srv._uid, setTimeout(async () => {
    if (!srv.url) return
    try {
      const { data } = await api.post(`/settings/media-servers/${srv.id}/test`)
      if (data.server_type && data.server_type !== 'unknown') {
        srv.type = data.server_type
      }
    } catch { /* silent — user can still select manually */ }
  }, 800))
}

// Watch each server's URL for changes (deep watch on the array)
watch(
  () => mediaServers.value.map(s => s.url),
  (newUrls, oldUrls) => {
    if (!oldUrls) return
    newUrls.forEach((url, i) => {
      if (url !== oldUrls[i]) scheduleAutoDetect(mediaServers.value[i])
    })
  }
)
```

- [ ] **Step 5: Update TABS to use service icons**

The current TABS array (lines 495–503) uses Font Awesome generic icons. Replace with service name data and update the tab bar template:

Update TABS:
```js
const TABS = [
  { id: 'general',  icon: 'fa-cog',    label: 'Général',      service: null },
  { id: 'servers',  icon: 'fa-server', label: 'Serveurs',     service: null },
  { id: 'radarr',   icon: null,        label: 'Radarr',       service: 'radarr' },
  { id: 'sonarr',   icon: null,        label: 'Sonarr',       service: 'sonarr' },
  { id: 'seerr',    icon: null,        label: 'Seerr',        service: 'overseerr' },
  { id: 'qbit',     icon: null,        label: 'qBittorrent',  service: 'qbittorrent' },
  { id: 'discord',  icon: null,        label: 'Discord',      service: 'discord' },
]
```

Update tab bar template (replace the `<i>` tag inside the tab button):
```html
<button
  v-for="tab in TABS"
  :key="tab.id"
  @click="activeTab = tab.id"
  class="flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors"
  :class="activeTab === tab.id ? 'bg-[var(--accent)] text-white' : 'text-[var(--muted)] hover:text-white hover:bg-[var(--bg3)]'"
>
  <ServiceIcon v-if="tab.service" :name="tab.service" :size="14" :color="activeTab === tab.id ? '#fff' : undefined" />
  <i v-else :class="['fas', tab.icon, 'text-xs']" />
  <span>{{ tab.label }}</span>
</button>
```

- [ ] **Step 6: Update qBittorrent tab icon to show Qui when proxy URL configured**

In the TABS computed, make the qbit service reactive based on `form.value.qbit_proxy_url`:

Replace the static TABS `const` with a computed:
```js
const TABS = computed(() => [
  { id: 'general',  icon: 'fa-cog',    label: 'Général',      service: null },
  { id: 'servers',  icon: 'fa-server', label: 'Serveurs',     service: null },
  { id: 'radarr',   icon: null,        label: 'Radarr',       service: 'radarr' },
  { id: 'sonarr',   icon: null,        label: 'Sonarr',       service: 'sonarr' },
  { id: 'seerr',    icon: null,        label: 'Seerr',        service: 'overseerr' },
  { id: 'qbit',     icon: null,        label: 'qBittorrent',  service: form.value.qbit_proxy_url ? 'qui' : 'qbittorrent' },
  { id: 'discord',  icon: null,        label: 'Discord',      service: 'discord' },
])
```

- [ ] **Step 7: Add Qui icon to qBittorrent section header**

In the qBittorrent settings section template (find the section for tab `qbit`), add a header with the Qui icon when a proxy URL is configured. Find the qbit section and add at the top:

```html
<!-- qBit proxy badge -->
<div v-if="form.qbit_proxy_url" class="flex items-center gap-2 mb-3 px-3 py-2 bg-purple-500/10 border border-purple-500/20 rounded-lg text-xs text-purple-300">
  <ServiceIcon name="qui" :size="14" />
  Interface Qui active (proxy URL configuré)
</div>
```

- [ ] **Step 8: Build and verify**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | tail -8
```

Expected: successful build with no errors.

- [ ] **Step 9: Deploy frontend**

```bash
docker cp /opt/claude/hygie/frontend/vue/dist/. hygie:/app/frontend/dist/ && docker restart hygie
```

Expected: container restarts and frontend serves correctly.

---

## Task 3: Backend Quick Fixes

**Files:**
- Modify: `backend/db/schema.py` (remove duplicate guard)
- Modify: `backend/emby_client.py` (move imports + fix pagination)

- [ ] **Step 1: Remove duplicate guard in schema.py**

`/opt/claude/hygie/backend/db/schema.py` lines 577–581 have two identical `if not rows: return 0` blocks. Remove the second one:

```python
# BEFORE (lines 577-581):
    if not rows:
        return 0

    if not rows:
        return 0

# AFTER (keep only one):
    if not rows:
        return 0
```

- [ ] **Step 2: Move local imports to module top in emby_client.py**

Currently `_decrypt_value` is imported inside two function bodies (`get_client` at line 33, `get_client_ext_url` at line 52). Move to module top.

Add after existing imports (after `from .db.utils import TIMEOUT_SHORT, TIMEOUT_MEDIUM, TIMEOUT_LONG, http_retry`):

```python
from .db.encryption import _decrypt_value
```

Then remove both `from .db.encryption import _decrypt_value` lines inside the function bodies (lines 33 and 52).

- [ ] **Step 3: Fix Limit:100000 in get_library_user_data with pagination**

Replace the current `get_library_user_data` function body with paginated fetching:

```python
async def get_library_user_data(user_id: str, library_id: str, server_id: str = "0") -> dict:
    """Return {emby_item_id: UserData} for all items in a library for one user."""
    url, key = await get_client(server_id)
    if not url or not key:
        return {}

    result: dict = {}
    start_index = 0
    page_size = 500

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_LONG) as client:
            while True:
                params = {
                    "ParentId": library_id,
                    "Fields": "UserData",
                    "Recursive": "true",
                    "StartIndex": start_index,
                    "Limit": page_size,
                    "IncludeItemTypes": "Movie,Episode",
                }
                r = await http_retry(
                    lambda: client.get(f"{url}/Users/{user_id}/Items", headers=_auth(key), params=params)
                )
                if r.status_code != 200:
                    break
                body = r.json()
                items = body.get("Items", [])
                for item in items:
                    result[item["Id"]] = item.get("UserData") or {}
                total = body.get("TotalRecordCount", 0)
                start_index += len(items)
                if start_index >= total or not items:
                    break
    except Exception as e:
        logger.warning(f"get_library_user_data error: {e}")
    return result
```

- [ ] **Step 4: Deploy backend fixes**

```bash
docker cp /opt/claude/hygie/backend/db/schema.py hygie:/app/backend/db/schema.py
docker cp /opt/claude/hygie/backend/emby_client.py hygie:/app/backend/emby_client.py
docker restart hygie
sleep 3 && docker logs hygie --tail 20
```

Expected: container starts cleanly, no import errors.

---

## Task 4: Extract Health Router from main.py

**Files:**
- Create: `backend/routers/health.py`
- Modify: `backend/main.py`

The health endpoint in `main.py` is 55+ lines of logic that doesn't belong in the app entry point.

- [ ] **Step 1: Create backend/routers/health.py**

```python
"""Health check endpoint — DB, scheduler, disk, encryption."""
import logging
import os
from datetime import datetime, timezone

import aiosqlite
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..db.utils import DB_PATH
from ..version import VERSION

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)

_scheduler_ref = None  # set by main.py after scheduler is created


def set_scheduler(scheduler) -> None:
    global _scheduler_ref
    _scheduler_ref = scheduler


@router.get("/health")
async def health():
    """Public healthcheck — for Uptime Kuma, Docker, etc."""
    status_info = {
        "status": "healthy",
        "version": VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # DB check
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'"
            ) as cur:
                row = await cur.fetchone()
                status_info["database"] = f"{row[0]} tables" if row else "empty"
    except Exception as e:
        status_info["database"] = f"error: {e}"
        status_info["status"] = "degraded"

    # Scheduler check
    try:
        if _scheduler_ref is not None:
            jobs = {j.id: j for j in _scheduler_ref.get_jobs()}
            critical = ("scan_job", "deletion_job")
            missing = [jid for jid in critical if jid not in jobs or jobs[jid].next_run_time is None]
            if missing:
                status_info["scheduler"] = f"degraded (jobs sans next_run: {', '.join(missing)})"
                status_info["status"] = "degraded"
            else:
                status_info["scheduler"] = f"{len(jobs)} jobs"
        else:
            status_info["scheduler"] = "unavailable"
    except Exception:
        status_info["scheduler"] = "unavailable"

    # Disk check
    try:
        import shutil
        total, used, free = shutil.disk_usage(os.path.dirname(DB_PATH))
        free_mb = free // (1024 * 1024)
        status_info["disk_free_mb"] = free_mb
        if free_mb < 50:
            status_info["disk"] = "low"
            status_info["status"] = "degraded"
        else:
            status_info["disk"] = "ok"
    except Exception:
        status_info["disk"] = "unavailable"

    # Encryption check
    if os.environ.get("HYGIE_ENCRYPTION_KEY"):
        status_info["encryption"] = "enabled"
    else:
        status_info["encryption"] = "disabled (HYGIE_ENCRYPTION_KEY not set)"
        if status_info["status"] == "healthy":
            status_info["status"] = "degraded"

    code = 200 if status_info["status"] == "healthy" else 503
    return JSONResponse(status_info, status_code=code)
```

- [ ] **Step 2: Update main.py to use the health router**

In `backend/main.py`:

1. Add `health` to the router imports block:
```python
from .routers import (
    auth, backup, calendar, expert_rules, health as health_router,
    ignored, libraries, logs, media, metrics, seerr_rules,
    settings, stats, storage, unmonitored,
)
```

2. After `scheduler` is created in the lifespan context manager (find `scheduler = AsyncIOScheduler(...)`), add:
```python
health_router.set_scheduler(scheduler)
```

3. In the `app.include_router(...)` block, add:
```python
app.include_router(health_router.router)
```

4. Delete the `@app.get("/health")` function and its body from `main.py` (the old ~55-line health function).

- [ ] **Step 3: Add health router to routers/__init__.py if needed**

Check if `backend/routers/__init__.py` needs to be updated to export the new health module.

```bash
cat /opt/claude/hygie/backend/routers/__init__.py
```

If it has explicit exports, add `health` to them.

- [ ] **Step 4: Deploy**

```bash
docker cp /opt/claude/hygie/backend/routers/health.py hygie:/app/backend/routers/health.py
docker cp /opt/claude/hygie/backend/main.py hygie:/app/backend/main.py
docker restart hygie
sleep 3 && docker logs hygie --tail 15 && curl -s http://localhost:8096/health | python3 -m json.tool 2>/dev/null || curl -s http://localhost:8080/health
```

Expected: health endpoint responds with JSON `{"status": "healthy", ...}`.

---

## Task 5: MariaDB Migration Parity

**Files:**
- Modify: `backend/db/schema.py`

The `_migrate_libraries_to_expert_rules` function uses a raw `aiosqlite.Connection` directly (`async with db.execute(...)` syntax). This function is only called from `_init_db_sqlite` and never from `_init_db_mariadb`. Both issues need fixing.

- [ ] **Step 1: Understand the current signature**

`_migrate_libraries_to_expert_rules(db)` receives a raw `aiosqlite.Connection`. It reads from `libraries` and inserts into `expert_rules`. The MariaDB path in `_init_db_mariadb` uses the `DbConn` abstraction via `get_db()`.

- [ ] **Step 2: Refactor to use DbConn**

Replace the entire `_migrate_libraries_to_expert_rules` function to use `DbConn` instead of raw aiosqlite, and remove the `db` parameter (it will use `get_db()` internally):

```python
async def _migrate_libraries_to_expert_rules() -> int:
    """Convert library conditions → expert_rules rows (idempotent).
    
    Runs on both SQLite and MariaDB via DbConn.
    """
    from .engine import get_db
    async with get_db() as db:
        rows = await db.fetch_all(
            "SELECT id, name, conditions, logic, seerr_conditions, enabled FROM libraries"
        )

    if not rows:
        return 0

    created = 0
    ts = datetime.now(timezone.utc).isoformat()
    for row in rows:
        lib_id    = row["id"]
        name      = row["name"]
        conds_raw = row.get("conditions") or "[]"
        logic     = row.get("logic") or "AND"
        enabled   = row.get("enabled", 1)

        try:
            old_conds = json.loads(conds_raw)
        except Exception:
            continue

        if not old_conds:
            continue

        # Check if already migrated
        async with get_db() as db:
            existing = await db.fetch_one(
                "SELECT id FROM expert_rules WHERE library_id=?", (lib_id,)
            )
        if existing:
            continue

        # Convert condition format
        new_conditions = []
        for c in old_conds:
            field = c.get("field", "")
            op    = c.get("op", "gt")
            value = c.get("value", 0)
            if field:
                new_conditions.append({"field": field, "op": op, "value": value})

        if not new_conditions:
            continue

        conditions_json = json.dumps(new_conditions)
        rule_name = f"[Auto] {name}"

        async with get_db() as db:
            await db.execute(
                "INSERT INTO expert_rules "
                "(name, library_id, conditions, operator, action, enabled, priority, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (rule_name, lib_id, conditions_json, logic, "queue", int(enabled), 100, ts),
            )
            await db.commit()
        created += 1

    return created
```

- [ ] **Step 3: Update the call site in _init_db_sqlite**

Find the call `n = await _migrate_libraries_to_expert_rules(db)` in `_init_db_sqlite` and remove the `db` argument:

```python
n = await _migrate_libraries_to_expert_rules()
```

- [ ] **Step 4: Add call to _init_db_mariadb**

In `_init_db_mariadb`, after the `await db.commit()`:

```python
    await db.commit()
    n = await _migrate_libraries_to_expert_rules()
    if n:
        logger.info(f"Migration MariaDB : {n} règle(s) experte(s) créée(s)")
    logger.info("MariaDB schema initialized")
```

- [ ] **Step 5: Deploy**

```bash
docker cp /opt/claude/hygie/backend/db/schema.py hygie:/app/backend/db/schema.py
docker restart hygie
sleep 3 && docker logs hygie --tail 15
```

Expected: no errors, clean startup.

---

## Task 6: Extract `_build_expert_queue_entry` from scanner.py

**Files:**
- Modify: `backend/scanner.py`

The expert rules block in `_scan_library` (lines ~360–455) builds a queue entry dict inline — the same dict-building pattern used earlier for standard queue entries. Extracting it removes ~90 lines of duplication and makes the logic independently testable.

- [ ] **Step 1: Locate the current code**

```bash
grep -n "_build_expert_queue_entry\|expert.*queue\|queue.*entry" /opt/claude/hygie/backend/scanner.py | head -20
```

Read lines 355–460 of scanner.py to understand the current inline dict construction.

- [ ] **Step 2: Extract the helper**

Add this function near the top of scanner.py (after imports, before `_scan_library`):

```python
def _build_queue_entry(
    item: dict,
    library: dict,
    delete_at: str,
    detected_at: str,
    poster_url: str = "",
    tmdb_id: str = "",
    seerr_id=None,
    seerr_user_id=None,
    seerr_username: str = "",
    seerr_request_url: str = "",
    radarr_id=None,
    sonarr_id=None,
    sonarr_series_id=None,
    season_number=None,
) -> dict:
    """Build a media_queue entry dict from Emby item + library + enrichment data."""
    media_type = item.get("Type", "Movie")
    if media_type == "Episode":
        title_parts = [item.get("SeriesName") or item.get("Name", "")]
        if item.get("ParentIndexNumber") is not None:
            title_parts.append(f"S{item['ParentIndexNumber']:02d}")
        if item.get("IndexNumber") is not None:
            title_parts[-1] += f"E{item['IndexNumber']:02d}"
        title = " ".join(title_parts)
    else:
        title = item.get("Name", "")

    file_path = ""
    media_sources = item.get("MediaSources") or []
    if media_sources:
        file_path = media_sources[0].get("Path", "")

    added_date = item.get("DateCreated") or ""
    last_played = (item.get("UserData") or {}).get("LastPlayedDate") or ""

    return {
        "emby_id":          item.get("Id", ""),
        "title":            title,
        "media_type":       media_type,
        "library_id":       library.get("id", ""),
        "library_name":     library.get("name", ""),
        "file_path":        file_path,
        "poster_url":       poster_url,
        "tmdb_id":          tmdb_id,
        "seerr_id":         seerr_id,
        "seerr_user_id":    seerr_user_id,
        "seerr_username":   seerr_username,
        "seerr_request_url": seerr_request_url,
        "radarr_id":        radarr_id,
        "sonarr_id":        sonarr_id,
        "sonarr_series_id": sonarr_series_id,
        "season_number":    season_number,
        "detected_at":      detected_at,
        "delete_at":        delete_at,
        "added_date":       added_date,
        "last_played":      last_played,
    }
```

- [ ] **Step 3: Replace inline dict construction with the helper**

Find all places in `_scan_library` that build a `queue_entry = {...}` dict inline and replace them with calls to `_build_queue_entry(...)` passing the relevant arguments.

There are typically two call sites: one in the standard conditions path and one in the expert rules path.

- [ ] **Step 4: Verify build**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | tail -5
# Python syntax check:
python3 -c "import ast; ast.parse(open('/opt/claude/hygie/backend/scanner.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Deploy**

```bash
docker cp /opt/claude/hygie/backend/scanner.py hygie:/app/backend/scanner.py
docker restart hygie
sleep 3 && docker logs hygie --tail 10
```

---

## Task 7: ScanContext Dataclass for `_evaluate_item`

**Files:**
- Modify: `backend/conditions.py`
- Modify: `backend/scanner.py`

`_evaluate_item` currently takes 13 positional and keyword-only parameters. Adding a `ScanContext` dataclass groups the optional scan-level caches into a single object, making call sites readable and easier to extend.

- [ ] **Step 1: Read current signature**

```bash
grep -n "async def _evaluate_item\|def _evaluate_item" /opt/claude/hygie/backend/conditions.py
```

Read the full signature and understand which parameters are scan-level caches.

- [ ] **Step 2: Add ScanContext dataclass to conditions.py**

Add after imports, before `_evaluate_item`:

```python
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class ScanContext:
    """Scan-level caches passed into _evaluate_item to avoid per-item fetches."""
    radarr_path_cache: dict = field(default_factory=dict)
    sonarr_path_cache: dict = field(default_factory=dict)
    seerr_request_cache: dict = field(default_factory=dict)
    user_data_cache: dict = field(default_factory=dict)
    server_id: str = "0"
    dry_run: bool = False
```

- [ ] **Step 3: Update _evaluate_item signature**

Replace the current multiple keyword-only cache parameters with a single `ctx: ScanContext` parameter:

```python
async def _evaluate_item(
    item: dict,
    library: dict,
    *,
    ctx: ScanContext,
) -> Optional[dict]:
```

Inside `_evaluate_item`, replace all references to the old parameter names with `ctx.radarr_path_cache`, `ctx.sonarr_path_cache`, etc.

- [ ] **Step 4: Update all call sites in scanner.py**

Replace each `_evaluate_item(item, library, radarr_path_cache=..., ...)` call with:

```python
ctx = ScanContext(
    radarr_path_cache=radarr_path_cache,
    sonarr_path_cache=sonarr_path_cache,
    seerr_request_cache=seerr_request_cache,
    user_data_cache=user_data_cache,
    server_id=server_id,
    dry_run=dry_run,
)
result = await _evaluate_item(item, library, ctx=ctx)
```

Or build the `ctx` once per library scan and pass it to all item calls.

- [ ] **Step 5: Export ScanContext from conditions.py**

Confirm `ScanContext` is importable from `conditions`:

```python
from .conditions import ScanContext, _evaluate_item  # in scanner.py
```

- [ ] **Step 6: Syntax check and deploy**

```bash
python3 -c "import ast; ast.parse(open('/opt/claude/hygie/backend/conditions.py').read()); print('conditions OK')"
python3 -c "import ast; ast.parse(open('/opt/claude/hygie/backend/scanner.py').read()); print('scanner OK')"
docker cp /opt/claude/hygie/backend/conditions.py hygie:/app/backend/conditions.py
docker cp /opt/claude/hygie/backend/scanner.py hygie:/app/backend/scanner.py
docker restart hygie
sleep 3 && docker logs hygie --tail 10
```

---

## Task 8: Tests — pytest setup + rules engine + conditions

**Files:**
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_rules_engine.py`
- Create: `backend/tests/test_conditions.py`

- [ ] **Step 1: Install pytest in the project**

```bash
cd /opt/claude/hygie && pip install pytest pytest-asyncio 2>&1 | tail -5
```

Expected: installed successfully.

- [ ] **Step 2: Create test package**

```bash
mkdir -p /opt/claude/hygie/backend/tests
touch /opt/claude/hygie/backend/tests/__init__.py
```

- [ ] **Step 3: Create conftest.py**

```python
# backend/tests/conftest.py
import asyncio
import pytest

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
```

- [ ] **Step 4: Create test_rules_engine.py**

```python
# backend/tests/test_rules_engine.py
"""Tests for rules/engine.py — pure evaluation, no I/O."""
import pytest
from backend.rules.engine import evaluate_rule
from backend.rules.models import ExpertRule, Condition, RuleOperator, RuleAction


def make_rule(conditions, operator=RuleOperator.AND, action=RuleAction.QUEUE, enabled=True):
    return ExpertRule(
        name="test",
        conditions=conditions,
        operator=operator,
        action=action,
        enabled=enabled,
        priority=0,
    )


def test_single_condition_gt_passes():
    rule = make_rule([Condition(field="days_since_added", op="gt", value=30)])
    item = {"days_since_added": 45}
    assert evaluate_rule(rule, item) is True


def test_single_condition_gt_fails():
    rule = make_rule([Condition(field="days_since_added", op="gt", value=30)])
    item = {"days_since_added": 10}
    assert evaluate_rule(rule, item) is False


def test_single_condition_gte_boundary():
    rule = make_rule([Condition(field="days_since_added", op="gte", value=30)])
    item = {"days_since_added": 30}
    assert evaluate_rule(rule, item) is True


def test_single_condition_lt_passes():
    rule = make_rule([Condition(field="play_count", op="lt", value=5)])
    item = {"play_count": 2}
    assert evaluate_rule(rule, item) is True


def test_and_all_pass():
    rule = make_rule([
        Condition(field="days_since_added", op="gt", value=30),
        Condition(field="play_count", op="lt", value=5),
    ], operator=RuleOperator.AND)
    item = {"days_since_added": 45, "play_count": 2}
    assert evaluate_rule(rule, item) is True


def test_and_one_fails():
    rule = make_rule([
        Condition(field="days_since_added", op="gt", value=30),
        Condition(field="play_count", op="lt", value=5),
    ], operator=RuleOperator.AND)
    item = {"days_since_added": 45, "play_count": 10}
    assert evaluate_rule(rule, item) is False


def test_or_one_passes():
    rule = make_rule([
        Condition(field="days_since_added", op="gt", value=30),
        Condition(field="play_count", op="lt", value=5),
    ], operator=RuleOperator.OR)
    item = {"days_since_added": 10, "play_count": 2}
    assert evaluate_rule(rule, item) is True


def test_or_none_pass():
    rule = make_rule([
        Condition(field="days_since_added", op="gt", value=30),
        Condition(field="play_count", op="lt", value=5),
    ], operator=RuleOperator.OR)
    item = {"days_since_added": 10, "play_count": 10}
    assert evaluate_rule(rule, item) is False


def test_disabled_rule_never_matches():
    rule = make_rule([Condition(field="days_since_added", op="gt", value=0)], enabled=False)
    item = {"days_since_added": 999}
    assert evaluate_rule(rule, item) is False


def test_missing_field_treated_as_zero():
    rule = make_rule([Condition(field="play_count", op="gt", value=5)])
    item = {}  # no play_count key
    assert evaluate_rule(rule, item) is False


def test_eq_operator():
    rule = make_rule([Condition(field="season_number", op="eq", value=1)])
    assert evaluate_rule(rule, {"season_number": 1}) is True
    assert evaluate_rule(rule, {"season_number": 2}) is False


def test_empty_conditions_false():
    rule = make_rule([])
    assert evaluate_rule(rule, {"days_since_added": 999}) is False
```

- [ ] **Step 5: Run rules engine tests**

```bash
cd /opt/claude/hygie && python -m pytest backend/tests/test_rules_engine.py -v 2>&1
```

Expected: all 12 tests pass.

- [ ] **Step 6: Create test_conditions.py**

```python
# backend/tests/test_conditions.py
"""Tests for conditions.py — _evaluate_conditions logic (no I/O)."""
import pytest
from backend.conditions import _evaluate_conditions


def test_and_all_match():
    library = {
        "conditions": [{"field": "days_since_added", "op": "gt", "value": 30}],
        "logic": "AND",
    }
    item_data = {"days_since_added": 45}
    assert _evaluate_conditions(library, item_data) is True


def test_and_none_match():
    library = {
        "conditions": [{"field": "days_since_added", "op": "gt", "value": 30}],
        "logic": "AND",
    }
    item_data = {"days_since_added": 5}
    assert _evaluate_conditions(library, item_data) is False


def test_or_one_matches():
    library = {
        "conditions": [
            {"field": "days_since_added", "op": "gt", "value": 30},
            {"field": "play_count", "op": "lt", "value": 5},
        ],
        "logic": "OR",
    }
    item_data = {"days_since_added": 5, "play_count": 2}
    assert _evaluate_conditions(library, item_data) is True


def test_empty_conditions():
    library = {"conditions": [], "logic": "AND"}
    assert _evaluate_conditions(library, {"days_since_added": 999}) is False


def test_missing_field_defaults_zero():
    library = {
        "conditions": [{"field": "play_count", "op": "gt", "value": 0}],
        "logic": "AND",
    }
    assert _evaluate_conditions(library, {}) is False  # missing field → 0, not > 0
```

- [ ] **Step 7: Run conditions tests**

```bash
cd /opt/claude/hygie && python -m pytest backend/tests/test_conditions.py -v 2>&1
```

Expected: all 5 tests pass (adjust if `_evaluate_conditions` signature differs).

- [ ] **Step 8: Run full test suite**

```bash
cd /opt/claude/hygie && python -m pytest backend/tests/ -v 2>&1
```

Expected: all tests pass.

---

## Self-Review Checklist

**Spec coverage:**
- [x] Service brand icons (Task 1–2)
- [x] Auto-detect Emby/Jellyfin on URL change (Task 2)
- [x] Qui icon when proxy URL set (Task 2)
- [x] Schema.py double guard (Task 3)
- [x] emby_client.py local imports (Task 3)
- [x] Limit:100000 pagination (Task 3)
- [x] Split main.py health endpoint (Task 4)
- [x] MariaDB migration parity (Task 5)
- [x] Extract queue entry builder (Task 6)
- [x] ScanContext dataclass (Task 7)
- [x] Tests for rules/engine + conditions (Task 8)

**Items deferred (complex, low risk, own plan):**
- Unify rule systems (library conditions → expert rules): invasive, requires tests to be in place first (now added in Task 8)
- MediaServerClient abstract interface: architectural refactor, requires Plex integration plan first
- Split SettingsView.vue into sub-components: cosmetic, no bugs, very large refactor
