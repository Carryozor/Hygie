# Frontend Core Refactors — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extraire le polling de AppSidebar vers un store dédié, remplacer window.location.href par Vue Router dans l'intercepteur 401, et ajouter un système global de toasts pour les erreurs API.

**Architecture:** Trois changements indépendants. Le store `status.js` centralise l'état de santé du système. L'intercepteur 401 émet un événement DOM plutôt que de forcer un rechargement de page. Le handler d'erreurs intercepte les 422/500 Axios et déclenche des toasts via un événement DOM. Tout passe par App.vue comme point d'orchestration.

**Tech Stack:** Vue 3 `<script setup>`, Pinia, Axios interceptors, DOM CustomEvent

---

## File Map

| Action | Fichier | Rôle |
|---|---|---|
| Create | `frontend/vue/src/stores/status.js` | Polling scheduler + santé serveurs + logs non vus |
| Create | `frontend/vue/src/api/errorHandler.js` | Formateur d'erreurs API (422/500) |
| Create | `frontend/vue/src/components/ui/ToastNotification.vue` | Composant toast global |
| Modify | `frontend/vue/src/api/client.js` | 401 → événement DOM (pas window.location.href) |
| Modify | `frontend/vue/src/App.vue` | Écoute hygie:unauthorized + monte ToastNotification |
| Modify | `frontend/vue/src/components/layout/AppSidebar.vue` | Utilise useStatusStore(), supprime tout le polling |

---

## Task 1 : Créer stores/status.js

**Files:**
- Create: `frontend/vue/src/stores/status.js`

- [ ] **Step 1 : Créer le store**

```javascript
// frontend/vue/src/stores/status.js
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api from '@/api/client'
import { useServersStore } from '@/stores/servers'

export const useStatusStore = defineStore('status', () => {
  // ── Scheduler state ──────────────────────────────────────────────────────
  const scanNext       = ref(null)   // ISO string | null
  const deletionNext   = ref(null)   // ISO string | null
  const scanRunning    = ref(false)
  const deletionRunning = ref(false)

  // ── Server health state ──────────────────────────────────────────────────
  const serverError  = ref(false)
  const serverStatus = ref('none')   // 'none'|'ok'|'unknown'|'error'

  // ── Log state ────────────────────────────────────────────────────────────
  const hasUnseenErrors = ref(false)

  // ── Computed logo status ─────────────────────────────────────────────────
  const logoStatus = computed(() => {
    if (hasUnseenErrors.value)            return 'error'
    if (serverStatus.value === 'ok')      return 'ok'
    if (serverStatus.value === 'unknown') return 'unknown'
    if (serverStatus.value === 'error')   return 'unknown'
    return 'none'
  })

  // ── Intervals ─────────────────────────────────────────────────────────────
  let _schedulerInterval = null
  let _healthInterval    = null
  let _logsInterval      = null

  // ── Scheduler polling ─────────────────────────────────────────────────────
  async function fetchScheduler() {
    try {
      const { data } = await api.get('/scheduler/status')
      const scan     = data.find(j => j.id === 'scan_job')
      const deletion = data.find(j => j.id === 'deletion_job')
      scanNext.value      = scan?.next_run      || null
      deletionNext.value  = deletion?.next_run  || null
      scanRunning.value    = scan?.is_running    || false
      deletionRunning.value = deletion?.is_running || false

      const anyRunning = scanRunning.value || deletionRunning.value
      if (_schedulerInterval) {
        clearInterval(_schedulerInterval)
        _schedulerInterval = setInterval(fetchScheduler, anyRunning ? 3000 : 30000)
      }
    } catch { /* silent */ }
  }

  // ── Server health ─────────────────────────────────────────────────────────
  async function checkServerHealth() {
    const servers = useServersStore()
    const srvList = (servers.servers || []).filter(
      s => s.id !== undefined && s.enabled !== false
    )
    if (!srvList.length) {
      serverStatus.value = 'none'
      serverError.value  = false
      return
    }
    let ok = 0, fail = 0
    for (const srv of srvList) {
      try {
        const { data } = await api.post(`/settings/media-servers/${srv.id}/test`)
        data.ok ? ok++ : fail++
      } catch { fail++ }
    }
    serverError.value  = fail > 0
    serverStatus.value = fail === 0 ? 'ok' : ok > 0 ? 'unknown' : 'error'
  }

  // ── Unseen error logs ─────────────────────────────────────────────────────
  async function checkUnseenErrors() {
    try {
      const { data } = await api.get('/logs/unseen-errors-count')
      hasUnseenErrors.value = (data.count || 0) > 0
    } catch { /* silent */ }
  }

  // ── Lifecycle ─────────────────────────────────────────────────────────────
  async function start() {
    if (!localStorage.getItem('hygie_token')) return
    const servers = useServersStore()
    await servers.fetch()
    await fetchScheduler()
    checkServerHealth()
    checkUnseenErrors()

    _schedulerInterval = setInterval(fetchScheduler, 30000)
    _healthInterval    = setInterval(checkServerHealth, 120000)
    _logsInterval      = setInterval(checkUnseenErrors, 20000)
  }

  function stop() {
    clearInterval(_schedulerInterval)
    clearInterval(_healthInterval)
    clearInterval(_logsInterval)
    _schedulerInterval = null
    _healthInterval    = null
    _logsInterval      = null
  }

  return {
    scanNext, deletionNext, scanRunning, deletionRunning,
    serverError, serverStatus, hasUnseenErrors, logoStatus,
    fetchScheduler, checkServerHealth, checkUnseenErrors,
    start, stop,
  }
})
```

- [ ] **Step 2 : Vérifier que le fichier est syntaxiquement valide**

```bash
cd /opt/claude/hygie/frontend/vue
node --input-type=module < /dev/null 2>&1 || true
# Simplement vérifier qu'il n'y a pas d'erreur de syntaxe évidente
head -5 src/stores/status.js
```

- [ ] **Step 3 : Commit**

```bash
git add frontend/vue/src/stores/status.js
git commit -m "feat(store): add status.js Pinia store for system health polling

Extracts fetchScheduler, checkServerHealth, checkUnseenErrors from
AppSidebar into a dedicated store. Exposes start()/stop() lifecycle."
```

---

## Task 2 : Créer api/errorHandler.js + ToastNotification.vue

**Files:**
- Create: `frontend/vue/src/api/errorHandler.js`
- Create: `frontend/vue/src/components/ui/ToastNotification.vue`

- [ ] **Step 1 : Créer errorHandler.js**

```javascript
// frontend/vue/src/api/errorHandler.js
/**
 * Format Axios errors into human-readable French messages.
 * Emits 'hygie:error' on window for ToastNotification to display.
 */

export function formatApiError(err) {
  if (!err.response) return 'Erreur réseau — vérifiez la connexion'
  const { status, data } = err.response

  if (status === 422 && data?.detail) {
    if (Array.isArray(data.detail)) {
      return data.detail
        .map(e => {
          const field = e.loc?.filter(l => l !== 'body').join('.') || ''
          return field ? `${field} : ${e.msg}` : e.msg
        })
        .join(' | ')
    }
    return String(data.detail)
  }

  if (status === 500) return data?.detail || 'Erreur serveur interne'
  if (status === 404) return 'Ressource introuvable'
  if (status === 403) return 'Accès refusé'
  return `Erreur ${status}`
}

export function emitError(message, type = 'error') {
  window.dispatchEvent(new CustomEvent('hygie:error', {
    detail: { message, type }
  }))
}

export function installErrorInterceptor(axiosInstance) {
  axiosInstance.interceptors.response.use(
    r => r,
    err => {
      const status = err.response?.status
      // 401 handled by auth interceptor, skip
      if (status === 401) return Promise.reject(err)
      // Only surface 422 and 5xx to the user
      if (status === 422 || (status >= 500 && status < 600)) {
        emitError(formatApiError(err))
      }
      return Promise.reject(err)
    }
  )
}
```

- [ ] **Step 2 : Créer ToastNotification.vue**

```vue
<!-- frontend/vue/src/components/ui/ToastNotification.vue -->
<template>
  <Teleport to="body">
    <div class="fixed bottom-4 right-4 z-50 flex flex-col gap-2 pointer-events-none">
      <TransitionGroup name="toast">
        <div
          v-for="toast in toasts"
          :key="toast.id"
          class="pointer-events-auto flex items-start gap-3 px-4 py-3 rounded-xl shadow-lg text-sm max-w-sm border"
          :class="toastClass(toast.type)"
        >
          <i :class="['fas', toastIcon(toast.type), 'mt-0.5 flex-shrink-0']" />
          <span class="flex-1">{{ toast.message }}</span>
          <button
            @click="remove(toast.id)"
            class="flex-shrink-0 opacity-60 hover:opacity-100 transition-opacity"
          >
            <i class="fas fa-times text-xs" />
          </button>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'

const toasts = ref([])
let _counter = 0

function toastClass(type) {
  if (type === 'error')   return 'bg-red-500/15 border-red-500/30 text-red-300'
  if (type === 'warning') return 'bg-yellow-500/15 border-yellow-500/30 text-yellow-300'
  if (type === 'success') return 'bg-green-500/15 border-green-500/30 text-green-300'
  return 'bg-[var(--bg2)] border-[var(--border)] text-[var(--text)]'
}

function toastIcon(type) {
  if (type === 'error')   return 'fa-circle-exclamation'
  if (type === 'warning') return 'fa-triangle-exclamation'
  if (type === 'success') return 'fa-circle-check'
  return 'fa-info-circle'
}

function add(message, type = 'error') {
  const id = ++_counter
  toasts.value.push({ id, message, type })
  setTimeout(() => remove(id), 6000)
}

function remove(id) {
  toasts.value = toasts.value.filter(t => t.id !== id)
}

function onError(e) {
  add(e.detail.message, e.detail.type || 'error')
}

onMounted(()   => window.addEventListener('hygie:error', onError))
onUnmounted(() => window.removeEventListener('hygie:error', onError))
</script>

<style scoped>
.toast-enter-active,
.toast-leave-active { transition: all 0.3s ease; }
.toast-enter-from   { transform: translateX(100%); opacity: 0; }
.toast-leave-to     { transform: translateX(100%); opacity: 0; }
</style>
```

- [ ] **Step 3 : Commit**

```bash
git add frontend/vue/src/api/errorHandler.js frontend/vue/src/components/ui/ToastNotification.vue
git commit -m "feat(ui): add error handler and ToastNotification component

errorHandler.js: formats Axios 422/500 errors into French messages,
emits hygie:error DOM event.
ToastNotification.vue: listens for hygie:error, displays dismissible
toasts with auto-dismiss after 6s."
```

---

## Task 3 : Modifier api/client.js — 401 sans rechargement

**Files:**
- Modify: `frontend/vue/src/api/client.js`

- [ ] **Step 1 : Lire le fichier actuel**

```bash
cat /opt/claude/hygie/frontend/vue/src/api/client.js
```

- [ ] **Step 2 : Remplacer le contenu**

```javascript
// frontend/vue/src/api/client.js
import axios from 'axios'
import { installErrorInterceptor } from './errorHandler'

const api = axios.create({ baseURL: '/api' })

api.interceptors.request.use(cfg => {
  const token = localStorage.getItem('hygie_token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

api.interceptors.response.use(
  r => r,
  err => {
    const isPublic = ['/login', '/setup', '/public'].includes(window.location.pathname)
    if (err.response?.status === 401 && !isPublic) {
      localStorage.removeItem('hygie_token')
      // Emit event — App.vue handles navigation via Vue Router (no page reload)
      window.dispatchEvent(new Event('hygie:unauthorized'))
    }
    return Promise.reject(err)
  }
)

// Install global error toast interceptor (422, 5xx)
installErrorInterceptor(api)

export default api
```

- [ ] **Step 3 : Commit**

```bash
git add frontend/vue/src/api/client.js
git commit -m "fix(api): replace window.location.href with DOM event for 401

Emits hygie:unauthorized instead of forcing a full page reload.
App.vue handles navigation via Vue Router — no login loop possible.
Installs error interceptor for 422/5xx toast display."
```

---

## Task 4 : Modifier App.vue — orchestration

**Files:**
- Modify: `frontend/vue/src/App.vue`

- [ ] **Step 1 : Lire le fichier actuel**

```bash
cat /opt/claude/hygie/frontend/vue/src/App.vue
```

- [ ] **Step 2 : Remplacer le contenu**

```vue
<template>
  <div class="min-h-screen flex bg-[var(--bg)]">
    <AppSidebar v-if="showLayout" />
    <div class="flex-1 flex flex-col min-w-0">
      <AppTopbar v-if="showLayout" />
      <main class="flex-1 overflow-auto p-6">
        <router-view />
      </main>
    </div>
  </div>
  <ToastNotification />
</template>

<script setup>
import { computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useStatusStore } from '@/stores/status'
import AppSidebar        from '@/components/layout/AppSidebar.vue'
import AppTopbar         from '@/components/layout/AppTopbar.vue'
import ToastNotification from '@/components/ui/ToastNotification.vue'

const route  = useRoute()
const router = useRouter()
const auth   = useAuthStore()
const status = useStatusStore()

const showLayout = computed(() => !route.meta.public)

// Handle 401 without page reload — use Vue Router
function onUnauthorized() {
  auth.logout()
  router.push('/login')
}

onMounted(() => {
  window.addEventListener('hygie:unauthorized', onUnauthorized)
  if (auth.isLoggedIn) {
    auth.fetchMe()
    status.start()
  }
})

onUnmounted(() => {
  window.removeEventListener('hygie:unauthorized', onUnauthorized)
  status.stop()
})
</script>
```

- [ ] **Step 3 : Commit**

```bash
git add frontend/vue/src/App.vue
git commit -m "feat(app): orchestrate auth + status polling + toast from App.vue

- Listens for hygie:unauthorized → logout + router.push('/login') (no reload)
- Starts status polling on login (start()/stop() lifecycle)
- Mounts ToastNotification globally"
```

---

## Task 5 : Simplifier AppSidebar.vue

**Files:**
- Modify: `frontend/vue/src/components/layout/AppSidebar.vue`

**Objectif :** Supprimer toute logique de polling. AppSidebar devient un composant de présentation pur qui lit depuis `useStatusStore()`.

- [ ] **Step 1 : Lire le fichier actuel en entier**

```bash
cat /opt/claude/hygie/frontend/vue/src/components/layout/AppSidebar.vue
```

- [ ] **Step 2 : Identifier toutes les refs/computed/functions à supprimer**

À supprimer de `<script setup>` :
- `const serverError = ref(false)`
- `const serverStatus = ref('none')`
- `const hasUnseenErrors = ref(false)`
- `let logsInterval = null`
- `const logoStatus = computed(...)`
- `function checkUnseenErrors() {...}`
- `function checkServerHealth() {...}`
- `async function fetchScheduler() {...}` (garder seulement un wrapper qui délègue au store)
- Dans `onMounted` : supprimer les appels à ces fonctions et les setInterval correspondants
- Dans `onUnmounted` : supprimer clearInterval pour ces intervals

À garder / adapter dans `<script setup>` :
- `import { useStatusStore } from '@/stores/status'`
- `const statusStore = useStatusStore()`
- Remplacer toutes les refs locales par les équivalents du store :
  - `scanRunning` → `statusStore.scanRunning`
  - `deletionRunning` → `statusStore.deletionRunning`
  - `scanNext` → `statusStore.scanNext`
  - `deletionNext` → `statusStore.deletionNext`
  - `hasUnseenErrors` → `statusStore.hasUnseenErrors`
  - `logoStatus` → `statusStore.logoStatus`
- La fonction `triggerScan` peut appeler `statusStore.fetchScheduler()` après le trigger
- Garder `now`, `clockInterval`, `version`, `isDryRun`, `triggering` — ce sont des états propres à la sidebar

- [ ] **Step 3 : Réécrire le script setup simplifié**

Le nouveau `<script setup>` de AppSidebar doit ressembler à ceci (adapter selon le code réel lu à l'étape 1) :

```javascript
<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useServersStore } from '@/stores/servers'
import { useSettingsStore } from '@/stores/settings'
import { useStatusStore } from '@/stores/status'
import HygieLogoSvg from '@/components/ui/HygieLogoSvg.vue'
import ServerLibraryTree from './ServerLibraryTree.vue'
import api from '@/api/client'

const servers  = useServersStore()
const settings = useSettingsStore()
const status   = useStatusStore()

const version      = ref('')
const now          = ref(new Date())
const isDryRun     = ref(false)
const togglingDryRun = ref(false)
const triggering   = ref(null)

let clockInterval = null

// Dériver les données du status store
const scanNext        = computed(() => status.scanNext)
const deletionNext    = computed(() => status.deletionNext)
const scanRunning     = computed(() => status.scanRunning)
const deletionRunning = computed(() => status.deletionRunning)
const hasUnseenErrors = computed(() => status.hasUnseenErrors)
const logoStatus      = computed(() => status.logoStatus)

// ... reste des computed (scanProgress, deletionProgress, scanCountdown, etc.)
// ... fonctions triggerScan, triggerDeletion, toggleDryRun

const navItems = [/* ... garder tel quel */]

onMounted(async () => {
  if (!localStorage.getItem('hygie_token')) return
  await settings.fetch()
  try {
    const { data } = await api.get('/version')
    version.value = data.version || ''
  } catch { /* silent */ }
  try {
    const s = settings.settings
    isDryRun.value = s.dry_run === 'true' || s.dry_run === true
  } catch { /* silent */ }
  clockInterval = setInterval(() => { now.value = new Date() }, 10000)
})

onUnmounted(() => {
  clearInterval(clockInterval)
})
</script>
```

**Note :** La sidebar ne gère plus aucun polling. `status.start()` est appelé depuis `App.vue`. Si la sidebar doit déclencher un refresh du scheduler après un trigger manuel, elle appelle `status.fetchScheduler()`.

- [ ] **Step 4 : Vérifier dans le template que toutes les refs utilisées existent**

Chercher dans le template toutes les variables et s'assurer qu'elles sont définies (soit localement soit via le status store) :
- `scanNext`, `scanRunning`, `scanCountdown`, `scanProgress` → via store ou computed locaux
- `deletionNext`, `deletionRunning`, `deletionCountdown`, `deletionProgress` → idem
- `hasUnseenErrors`, `logoStatus` → via store
- `version`, `navItems`, `isDryRun`, `triggering`, `now` → locaux

- [ ] **Step 5 : Build pour vérifier pas d'erreurs**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | tail -10
```

Résultat attendu : `✅ built in X.XXs` sans erreurs TypeScript/Vue.

- [ ] **Step 6 : Deployer et tester**

```bash
cd /opt/claude/hygie && make deploy
```

Ouvrir le navigateur et vérifier :
- Le logo s'affiche correctement
- La sidebar montre les timers de scan/deletion
- Pas d'erreurs dans la console browser

- [ ] **Step 7 : Commit**

```bash
git add frontend/vue/src/components/layout/AppSidebar.vue
git commit -m "refactor(sidebar): extract polling to useStatusStore

AppSidebar is now a pure presentation component (~180 lines vs 304).
All polling (scheduler, server health, unseen errors) delegated to
useStatusStore via computed refs. No local intervals."
```

---

## Self-Review Checklist

- [x] **Spec coverage :** statusStore ✓, intercepteur 401 sans reload ✓, toasts erreurs ✓, App.vue orchestration ✓, AppSidebar simplifié ✓
- [x] **Pas de circular imports :** client.js → errorHandler.js (OK), status.js → servers.js (OK via useServersStore), App.vue → stores (OK)
- [x] **Event lifecycle :** addEventListener/removeEventListener en paire dans onMounted/onUnmounted
- [x] **Guard token :** start() vérifie localStorage.getItem('hygie_token') avant tout appel API
- [x] **Build requis avant deploy :** Task 5 inclut `npm run build` explicite
