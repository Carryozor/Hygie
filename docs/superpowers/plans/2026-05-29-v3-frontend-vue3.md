# Hygie v3.0 — Phase 3: Vue 3 Frontend Migration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the existing vanilla JS + Jinja2 frontend with a Vue 3 + Vite SPA while keeping the same FastAPI backend serving the same API endpoints.

**Architecture:** Vite builds the Vue 3 SPA into `frontend/dist/`. FastAPI serves `frontend/dist/index.html` for all non-API routes (existing `StaticFiles` mount stays but points to `dist/`). Pinia manages app state. Vue Router handles client-side navigation. The Layout B design is implemented: sidebar with server-grouped library tree, top nav, dashboard cross-server main panel. Auth is JWT-based (same as before). The existing Jinja2 `index.html` is kept as a fallback during migration but replaced once Vue is stable.

**Tech Stack:** Vue 3.4+, Vite 5, Pinia 2, Vue Router 4, TailwindCSS 3 (via PostCSS, replacing CDN), Axios 1.x (for API calls), @vueuse/core (utilities), Node.js 20 LTS for build

**Prerequisite:** Phases 1 and 2 complete (but not strictly required — the frontend is independent of backend DB/Plex changes as long as API contracts are unchanged).

---

## File Structure

```
frontend/
├── vue/                        ← Vue 3 source (new)
│   ├── index.html              ← Vite entry HTML
│   ├── vite.config.js          ← Vite config (proxy API to backend)
│   ├── tailwind.config.js      ← TailwindCSS config
│   ├── postcss.config.js       ← PostCSS with Tailwind + autoprefixer
│   ├── package.json            ← Node deps
│   ├── src/
│   │   ├── main.js             ← Vue app bootstrap
│   │   ├── App.vue             ← Root component (router-view)
│   │   ├── router/index.js     ← Vue Router routes
│   │   ├── stores/
│   │   │   ├── auth.js         ← Pinia: JWT token, user
│   │   │   ├── settings.js     ← Pinia: app settings
│   │   │   ├── servers.js      ← Pinia: media servers list
│   │   │   ├── libraries.js    ← Pinia: libraries by server
│   │   │   ├── queue.js        ← Pinia: media queue + filters
│   │   │   └── stats.js        ← Pinia: global + per-library stats
│   │   ├── api/
│   │   │   └── client.js       ← Axios instance + auth interceptor
│   │   ├── components/
│   │   │   ├── layout/
│   │   │   │   ├── AppSidebar.vue      ← Layout B sidebar
│   │   │   │   ├── AppTopbar.vue       ← Top navigation bar
│   │   │   │   └── ServerLibraryTree.vue  ← Grouped library list
│   │   │   ├── ui/
│   │   │   │   ├── ToggleSlider.vue    ← Reusable toggle (green/red)
│   │   │   │   ├── StatCard.vue        ← Dashboard stat card
│   │   │   │   └── HygieLogoSvg.vue    ← Triple arc SVG logo
│   │   │   └── media/
│   │   │       ├── MediaTable.vue      ← Paginated queue table
│   │   │       └── MediaRow.vue        ← Single queue row
│   │   └── views/
│   │       ├── SetupView.vue           ← First-run setup
│   │       ├── LoginView.vue           ← Auth login
│   │       ├── DashboardView.vue       ← Cross-server dashboard (Layout B)
│   │       ├── LibraryView.vue         ← Per-library detail
│   │       ├── QueueView.vue           ← Full media queue
│   │       ├── RulesView.vue           ← Rule list (simple + expert)
│   │       ├── SettingsView.vue        ← Settings page
│   │       └── LogsView.vue            ← Log viewer
├── dist/                       ← Vite build output (gitignored)
└── static/                     ← Legacy static (kept for non-Vue assets)
```

**Modified backend files:**
- `backend/main.py` — mount `frontend/dist` instead of legacy templates; SPA fallback route
- `Dockerfile` — add Node build step before Python image

---

### Task 1: Vite project setup + TailwindCSS

**Files:**
- Create: `frontend/vue/package.json`
- Create: `frontend/vue/vite.config.js`
- Create: `frontend/vue/tailwind.config.js`
- Create: `frontend/vue/postcss.config.js`
- Create: `frontend/vue/index.html`

- [ ] **Step 1: Create `frontend/vue/package.json`**

```json
{
  "name": "hygie-frontend",
  "version": "3.0.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "axios": "^1.7.2",
    "pinia": "^2.1.7",
    "vue": "^3.4.29",
    "vue-router": "^4.3.3",
    "@vueuse/core": "^10.11.0"
  },
  "devDependencies": {
    "@vitejs/plugin-vue": "^5.0.5",
    "autoprefixer": "^10.4.19",
    "postcss": "^8.4.38",
    "tailwindcss": "^3.4.4",
    "vite": "^5.3.1"
  }
}
```

- [ ] **Step 2: Create `frontend/vue/vite.config.js`**

```javascript
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    outDir: '../dist',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/health': { target: 'http://localhost:8000', changeOrigin: true },
      '/static': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
```

- [ ] **Step 3: Create TailwindCSS + PostCSS config**

```javascript
// frontend/vue/tailwind.config.js
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,js}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Hygie dark theme palette (matching existing CSS vars)
        bg:     '#0f1117',
        bg2:    '#1a1d27',
        bg3:    '#22263a',
        accent: '#6366f1',
        muted:  '#8b92b3',
        danger: '#ef4444',
        success:'#22c55e',
      },
    },
  },
  plugins: [],
}
```

```javascript
// frontend/vue/postcss.config.js
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
```

- [ ] **Step 4: Create `frontend/vue/index.html`**

```html
<!DOCTYPE html>
<html lang="fr" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Hygie</title>
  <link rel="icon" type="image/svg+xml" href="/static/favicon.svg" />
</head>
<body class="bg-bg text-white">
  <div id="app"></div>
  <script type="module" src="/src/main.js"></script>
</body>
</html>
```

- [ ] **Step 5: Install dependencies and verify build works**

```bash
cd /opt/claude/hygie/frontend/vue && npm install
npm run build 2>&1 | tail -10
```
Expected: `✓ built in ...ms`, `dist/` created with `index.html` + assets

- [ ] **Step 6: Add `.gitignore` entry for dist**

```bash
echo "frontend/dist/" >> /opt/claude/hygie/.gitignore
echo "frontend/vue/node_modules/" >> /opt/claude/hygie/.gitignore
```

- [ ] **Step 7: Commit**

```bash
cd /opt/claude/hygie && git add frontend/vue/ .gitignore
git commit -m "feat(frontend): Vite + Vue 3 + TailwindCSS project scaffold"
```

---

### Task 2: API client + Pinia stores (auth, settings, servers)

**Files:**
- Create: `frontend/vue/src/api/client.js`
- Create: `frontend/vue/src/stores/auth.js`
- Create: `frontend/vue/src/stores/settings.js`
- Create: `frontend/vue/src/stores/servers.js`

- [ ] **Step 1: Create `frontend/vue/src/api/client.js`**

```javascript
// frontend/vue/src/api/client.js
import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

// Attach JWT to every request
api.interceptors.request.use(cfg => {
  const token = localStorage.getItem('hygie_token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

// On 401 → clear token and redirect to login
api.interceptors.response.use(
  r => r,
  err => {
    if (err.response?.status === 401) {
      localStorage.removeItem('hygie_token')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)

export default api
```

- [ ] **Step 2: Create `frontend/vue/src/stores/auth.js`**

```javascript
// frontend/vue/src/stores/auth.js
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api from '@/api/client'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('hygie_token') || '')
  const username = ref('')
  const setupComplete = ref(null)  // null = unknown

  const isLoggedIn = computed(() => !!token.value)

  async function checkSetup() {
    const { data } = await api.get('/auth/status')
    setupComplete.value = data.setup_complete
    return data.setup_complete
  }

  async function setup(u, p) {
    const { data } = await api.post('/auth/setup', { username: u, password: p })
    token.value = data.token
    username.value = data.username
    localStorage.setItem('hygie_token', data.token)
    setupComplete.value = true
  }

  async function login(u, p) {
    const { data } = await api.post('/auth/login', { username: u, password: p })
    token.value = data.token
    username.value = data.username || u
    localStorage.setItem('hygie_token', data.token)
  }

  async function fetchMe() {
    if (!token.value) return
    const { data } = await api.get('/auth/me')
    username.value = data.username
  }

  function logout() {
    token.value = ''
    username.value = ''
    localStorage.removeItem('hygie_token')
  }

  return { token, username, setupComplete, isLoggedIn, checkSetup, setup, login, fetchMe, logout }
})
```

- [ ] **Step 3: Create `frontend/vue/src/stores/settings.js`**

```javascript
// frontend/vue/src/stores/settings.js
import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api/client'

export const useSettingsStore = defineStore('settings', () => {
  const settings = ref({})
  const loading = ref(false)

  async function fetch() {
    loading.value = true
    try {
      const { data } = await api.get('/settings')
      settings.value = data
    } finally {
      loading.value = false
    }
  }

  async function save(patch) {
    await api.post('/settings', patch)
    Object.assign(settings.value, patch)
  }

  return { settings, loading, fetch, save }
})
```

- [ ] **Step 4: Create `frontend/vue/src/stores/servers.js`**

```javascript
// frontend/vue/src/stores/servers.js
import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api/client'

export const useServersStore = defineStore('servers', () => {
  const servers = ref([])   // [{id, name, type, url, enabled}, ...]
  const libraries = ref([]) // [{id, name, server_id, ...}, ...]

  async function fetch() {
    const [srvRes, libRes] = await Promise.all([
      api.get('/settings').then(r => r.data.media_servers || []),
      api.get('/libraries'),
    ])
    servers.value = typeof srvRes === 'string' ? JSON.parse(srvRes) : srvRes
    libraries.value = libRes.data || []
  }

  function librariesForServer(serverId) {
    return libraries.value.filter(l => String(l.server_id) === String(serverId))
  }

  return { servers, libraries, fetch, librariesForServer }
})
```

- [ ] **Step 5: Build to verify no syntax errors**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | grep -E "error|Error|✓" | head -10
```
Expected: only `✓ built` lines, no errors

- [ ] **Step 6: Commit**

```bash
cd /opt/claude/hygie && git add frontend/vue/src/
git commit -m "feat(frontend): Pinia stores for auth, settings, servers + Axios client"
```

---

### Task 3: Vue Router + App shell + Layout B components

**Files:**
- Create: `frontend/vue/src/main.js`
- Create: `frontend/vue/src/App.vue`
- Create: `frontend/vue/src/router/index.js`
- Create: `frontend/vue/src/components/layout/AppSidebar.vue`
- Create: `frontend/vue/src/components/layout/AppTopbar.vue`
- Create: `frontend/vue/src/components/layout/ServerLibraryTree.vue`
- Create: `frontend/vue/src/components/ui/HygieLogoSvg.vue`

- [ ] **Step 1: Create `frontend/vue/src/main.js`**

```javascript
// frontend/vue/src/main.js
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import './style.css'  // Tailwind base

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
```

- [ ] **Step 2: Create `frontend/vue/src/style.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  --bg: #0f1117;
  --bg2: #1a1d27;
  --bg3: #22263a;
  --accent: #6366f1;
  --muted: #8b92b3;
  --border: #2d3256;
}

body {
  background: var(--bg);
  color: #e2e8f0;
  font-family: 'Inter', system-ui, sans-serif;
}

/* Scrollbar */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: var(--bg2); }
::-webkit-scrollbar-thumb { background: var(--bg3); border-radius: 3px; }
```

- [ ] **Step 3: Create `frontend/vue/src/router/index.js`**

```javascript
// frontend/vue/src/router/index.js
import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes = [
  { path: '/setup',     name: 'setup',     component: () => import('@/views/SetupView.vue'),     meta: { public: true } },
  { path: '/login',     name: 'login',     component: () => import('@/views/LoginView.vue'),     meta: { public: true } },
  { path: '/',          name: 'dashboard', component: () => import('@/views/DashboardView.vue')  },
  { path: '/library/:id', name: 'library', component: () => import('@/views/LibraryView.vue')   },
  { path: '/queue',     name: 'queue',     component: () => import('@/views/QueueView.vue')      },
  { path: '/rules',     name: 'rules',     component: () => import('@/views/RulesView.vue')      },
  { path: '/settings',  name: 'settings',  component: () => import('@/views/SettingsView.vue')   },
  { path: '/logs',      name: 'logs',      component: () => import('@/views/LogsView.vue')       },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async to => {
  const auth = useAuthStore()
  if (to.meta.public) return true

  const setup = await auth.checkSetup()
  if (!setup) return { name: 'setup' }
  if (!auth.isLoggedIn) return { name: 'login', query: { redirect: to.fullPath } }
  return true
})

export default router
```

- [ ] **Step 4: Create `frontend/vue/src/App.vue`**

```vue
<!-- frontend/vue/src/App.vue -->
<template>
  <div class="min-h-screen flex bg-[var(--bg)]">
    <!-- Sidebar — hidden on auth pages -->
    <AppSidebar v-if="showLayout" />

    <div class="flex-1 flex flex-col min-w-0">
      <AppTopbar v-if="showLayout" />
      <main class="flex-1 overflow-auto p-6">
        <router-view />
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import AppTopbar  from '@/components/layout/AppTopbar.vue'

const route = useRoute()
const auth  = useAuthStore()

const showLayout = computed(() => !route.meta.public)

onMounted(() => { if (auth.isLoggedIn) auth.fetchMe() })
</script>
```

- [ ] **Step 5: Create `frontend/vue/src/components/ui/HygieLogoSvg.vue`**

Triple arc SVG logo (three concentric arcs forming a stylized "H"):

```vue
<!-- frontend/vue/src/components/ui/HygieLogoSvg.vue -->
<template>
  <svg :width="size" :height="size" viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
    <!-- Outer arc -->
    <path d="M 8 32 A 16 16 0 0 1 32 32" stroke="#6366f1" stroke-width="3" stroke-linecap="round" fill="none"/>
    <!-- Mid arc -->
    <path d="M 11 32 A 11 11 0 0 1 29 32" stroke="#818cf8" stroke-width="2.5" stroke-linecap="round" fill="none"/>
    <!-- Inner arc -->
    <path d="M 14 32 A 7 7 0 0 1 26 32" stroke="#a5b4fc" stroke-width="2" stroke-linecap="round" fill="none"/>
    <!-- Vertical stems of H -->
    <line x1="8" y1="14" x2="8" y2="32" stroke="#6366f1" stroke-width="3" stroke-linecap="round"/>
    <line x1="32" y1="14" x2="32" y2="32" stroke="#6366f1" stroke-width="3" stroke-linecap="round"/>
  </svg>
</template>

<script setup>
defineProps({ size: { type: Number, default: 40 } })
</script>
```

- [ ] **Step 6: Create `frontend/vue/src/components/layout/ServerLibraryTree.vue`**

```vue
<!-- frontend/vue/src/components/layout/ServerLibraryTree.vue -->
<template>
  <div class="space-y-4">
    <div v-for="server in servers.servers" :key="server.id">
      <!-- Server header -->
      <div class="flex items-center gap-2 px-3 py-1">
        <span
          class="w-2 h-2 rounded-full flex-shrink-0"
          :class="serverDotClass(server.type)"
        />
        <span class="text-xs font-semibold uppercase tracking-widest text-[var(--muted)] truncate">
          {{ server.name || 'Serveur' }}
        </span>
      </div>
      <!-- Libraries under this server -->
      <router-link
        v-for="lib in servers.librariesForServer(server.id)"
        :key="lib.id"
        :to="{ name: 'library', params: { id: lib.id } }"
        class="flex items-center gap-2 px-4 py-1.5 rounded-lg text-sm transition-colors hover:bg-[var(--bg3)] text-[var(--muted)] hover:text-white"
        active-class="bg-[var(--bg3)] text-white"
      >
        <i class="fas fa-film text-xs opacity-60" />
        <span class="truncate">{{ lib.name }}</span>
      </router-link>
    </div>
  </div>
</template>

<script setup>
import { useServersStore } from '@/stores/servers'
const servers = useServersStore()

function serverDotClass(type) {
  return {
    'bg-orange-400': type === 'plex',
    'bg-green-400':  type === 'jellyfin',
    'bg-blue-400':   type === 'emby',
    'bg-[var(--muted)]': !type,
  }
}
</script>
```

- [ ] **Step 7: Create `frontend/vue/src/components/layout/AppSidebar.vue`**

```vue
<!-- frontend/vue/src/components/layout/AppSidebar.vue -->
<template>
  <aside class="w-60 flex-shrink-0 flex flex-col border-r border-[var(--border)] bg-[var(--bg2)] h-screen sticky top-0 overflow-y-auto">
    <!-- Logo -->
    <div class="flex items-center gap-3 px-4 py-5 border-b border-[var(--border)]">
      <HygieLogoSvg :size="32" />
      <span class="font-bold text-lg tracking-tight">Hygie</span>
    </div>

    <!-- Main nav -->
    <nav class="flex-1 px-2 py-4 space-y-1">
      <router-link
        v-for="item in navItems"
        :key="item.to"
        :to="item.to"
        class="flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-[var(--muted)] hover:text-white hover:bg-[var(--bg3)] transition-colors"
        active-class="bg-[var(--bg3)] text-white"
        exact-active-class=""
      >
        <i :class="['fas', item.icon, 'w-4 text-center']" />
        <span>{{ item.label }}</span>
      </router-link>

      <div class="pt-4 pb-1 px-3">
        <span class="text-xs font-semibold uppercase tracking-widest text-[var(--muted)]">Bibliothèques</span>
      </div>
      <ServerLibraryTree />
    </nav>

    <!-- Bottom: dry run badge + settings -->
    <div class="px-3 py-4 border-t border-[var(--border)] space-y-2">
      <div
        v-if="isDryRun"
        class="flex items-center gap-2 px-3 py-2 rounded-lg bg-yellow-500/10 border border-yellow-500/30 text-yellow-400 text-xs"
      >
        <i class="fas fa-flask" />
        <span>Dry Run actif</span>
      </div>
      <router-link
        to="/settings"
        class="flex items-center gap-2 px-3 py-2 text-sm text-[var(--muted)] hover:text-white rounded-lg hover:bg-[var(--bg3)] transition-colors"
      >
        <i class="fas fa-cog w-4 text-center" />
        <span>Paramètres</span>
      </router-link>
    </div>
  </aside>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useServersStore } from '@/stores/servers'
import { useSettingsStore } from '@/stores/settings'
import HygieLogoSvg from '@/components/ui/HygieLogoSvg.vue'
import ServerLibraryTree from './ServerLibraryTree.vue'

const servers  = useServersStore()
const settings = useSettingsStore()

const isDryRun = computed(() => settings.settings.dry_run === 'true' || settings.settings.dry_run === true)

const navItems = [
  { to: '/',        icon: 'fa-chart-bar', label: 'Dashboard'   },
  { to: '/queue',   icon: 'fa-list',      label: 'File d\'attente' },
  { to: '/rules',   icon: 'fa-sliders-h', label: 'Règles'      },
  { to: '/logs',    icon: 'fa-scroll',    label: 'Journaux'    },
]

onMounted(async () => {
  await Promise.all([servers.fetch(), settings.fetch()])
})
</script>
```

- [ ] **Step 8: Create `frontend/vue/src/components/layout/AppTopbar.vue`**

```vue
<!-- frontend/vue/src/components/layout/AppTopbar.vue -->
<template>
  <header class="h-14 flex items-center justify-between px-6 border-b border-[var(--border)] bg-[var(--bg2)] flex-shrink-0">
    <h1 class="font-semibold text-base">{{ pageTitle }}</h1>
    <div class="flex items-center gap-4">
      <span class="text-sm text-[var(--muted)]">{{ auth.username }}</span>
      <button @click="auth.logout()" class="text-[var(--muted)] hover:text-white text-sm transition-colors">
        <i class="fas fa-sign-out-alt" />
      </button>
    </div>
  </header>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const auth  = useAuthStore()

const titles = {
  dashboard: 'Dashboard',
  library:   'Bibliothèque',
  queue:     'File d\'attente',
  rules:     'Règles',
  settings:  'Paramètres',
  logs:      'Journaux',
}
const pageTitle = computed(() => titles[route.name] || 'Hygie')
</script>
```

- [ ] **Step 9: Build to verify**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | tail -5
```
Expected: `✓ built`

- [ ] **Step 10: Commit**

```bash
cd /opt/claude/hygie && git add frontend/vue/src/
git commit -m "feat(frontend): Vue Router, App shell, Layout B sidebar + topbar + logo"
```

---

### Task 4: Auth views (Setup + Login) + Dashboard view

**Files:**
- Create: `frontend/vue/src/views/SetupView.vue`
- Create: `frontend/vue/src/views/LoginView.vue`
- Create: `frontend/vue/src/views/DashboardView.vue`
- Create: `frontend/vue/src/components/ui/StatCard.vue`
- Create: `frontend/vue/src/stores/stats.js`

- [ ] **Step 1: Create `frontend/vue/src/views/SetupView.vue`**

```vue
<!-- frontend/vue/src/views/SetupView.vue -->
<template>
  <div class="min-h-screen flex items-center justify-center bg-[var(--bg)]">
    <div class="w-full max-w-sm p-8 rounded-2xl bg-[var(--bg2)] border border-[var(--border)] space-y-6">
      <div class="flex flex-col items-center gap-3">
        <HygieLogoSvg :size="48" />
        <h1 class="text-2xl font-bold">Bienvenue sur Hygie</h1>
        <p class="text-[var(--muted)] text-sm text-center">Créez votre compte administrateur pour commencer.</p>
      </div>
      <form @submit.prevent="submit" class="space-y-4">
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Nom d'utilisateur</label>
          <input v-model="username" type="text" required autocomplete="username"
            class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-[var(--accent)]" />
        </div>
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Mot de passe</label>
          <input v-model="password" type="password" required autocomplete="new-password"
            class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-[var(--accent)]" />
        </div>
        <p v-if="error" class="text-red-400 text-xs">{{ error }}</p>
        <button type="submit" :disabled="loading"
          class="w-full bg-[var(--accent)] hover:opacity-90 disabled:opacity-50 rounded-lg py-2.5 text-sm font-semibold transition-opacity">
          {{ loading ? 'Création...' : 'Créer le compte' }}
        </button>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import HygieLogoSvg from '@/components/ui/HygieLogoSvg.vue'

const auth = useAuthStore()
const router = useRouter()
const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  loading.value = true
  try {
    await auth.setup(username.value, password.value)
    router.push('/')
  } catch (e) {
    error.value = e.response?.data?.detail || 'Erreur lors de la création du compte'
  } finally {
    loading.value = false
  }
}
</script>
```

- [ ] **Step 2: Create `frontend/vue/src/views/LoginView.vue`**

```vue
<!-- frontend/vue/src/views/LoginView.vue -->
<template>
  <div class="min-h-screen flex items-center justify-center bg-[var(--bg)]">
    <div class="w-full max-w-sm p-8 rounded-2xl bg-[var(--bg2)] border border-[var(--border)] space-y-6">
      <div class="flex flex-col items-center gap-3">
        <HygieLogoSvg :size="48" />
        <h1 class="text-xl font-bold">Connexion</h1>
      </div>
      <form @submit.prevent="submit" class="space-y-4">
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Nom d'utilisateur</label>
          <input v-model="username" type="text" required autocomplete="username"
            class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-[var(--accent)]" />
        </div>
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Mot de passe</label>
          <input v-model="password" type="password" required autocomplete="current-password"
            class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-[var(--accent)]" />
        </div>
        <p v-if="error" class="text-red-400 text-xs">{{ error }}</p>
        <button type="submit" :disabled="loading"
          class="w-full bg-[var(--accent)] hover:opacity-90 disabled:opacity-50 rounded-lg py-2.5 text-sm font-semibold transition-opacity">
          {{ loading ? 'Connexion...' : 'Se connecter' }}
        </button>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import HygieLogoSvg from '@/components/ui/HygieLogoSvg.vue'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  loading.value = true
  try {
    await auth.login(username.value, password.value)
    router.push(route.query.redirect || '/')
  } catch {
    error.value = 'Identifiants incorrects'
  } finally {
    loading.value = false
  }
}
</script>
```

- [ ] **Step 3: Create `frontend/vue/src/stores/stats.js`**

```javascript
// frontend/vue/src/stores/stats.js
import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api/client'

export const useStatsStore = defineStore('stats', () => {
  const global = ref({ total_deleted: 0, total_ignored: 0, total_scans: 0, queue: {}, by_month: [] })
  const storage = ref({ disks: [], movies: {}, series: {}, total_media_size: 0, queue: {} })
  const loading = ref(false)

  async function fetchGlobal() {
    loading.value = true
    try {
      const { data } = await api.get('/stats/global')
      global.value = data
    } finally {
      loading.value = false
    }
  }

  async function fetchStorage() {
    const { data } = await api.get('/storage')
    storage.value = data
  }

  return { global, storage, loading, fetchGlobal, fetchStorage }
})
```

- [ ] **Step 4: Create `frontend/vue/src/components/ui/StatCard.vue`**

```vue
<!-- frontend/vue/src/components/ui/StatCard.vue -->
<template>
  <div class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-5 flex flex-col gap-2">
    <div class="flex items-center justify-between">
      <span class="text-xs text-[var(--muted)] uppercase tracking-wide">{{ label }}</span>
      <i :class="['fas', icon, 'text-[var(--accent)] opacity-70']" />
    </div>
    <div class="text-3xl font-bold">{{ displayValue }}</div>
    <div v-if="sub" class="text-xs text-[var(--muted)]">{{ sub }}</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  label:    { type: String, required: true },
  value:    { type: [Number, String], default: 0 },
  icon:     { type: String, default: 'fa-chart-bar' },
  sub:      { type: String, default: '' },
  format:   { type: String, default: 'number' },  // 'number' | 'bytes' | 'string'
})

const displayValue = computed(() => {
  if (props.format === 'bytes') return formatBytes(Number(props.value))
  if (props.format === 'string') return props.value
  return Number(props.value).toLocaleString('fr-FR')
})

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`
}
</script>
```

- [ ] **Step 5: Create `frontend/vue/src/views/DashboardView.vue`**

```vue
<!-- frontend/vue/src/views/DashboardView.vue -->
<template>
  <div class="space-y-6">
    <!-- Stat cards row -->
    <div class="grid grid-cols-2 xl:grid-cols-4 gap-4">
      <StatCard
        label="File d'attente"
        :value="stats.global.queue?.pending ?? 0"
        icon="fa-clock"
        :sub="`${stats.global.queue?.deleted ?? 0} supprimés`"
      />
      <StatCard
        label="Espace récupérable"
        :value="stats.storage.queue?.reclaimable_size ?? 0"
        icon="fa-hdd"
        format="bytes"
      />
      <StatCard
        label="Suppressions totales"
        :value="stats.global.total_deleted ?? 0"
        icon="fa-trash"
      />
      <StatCard
        label="Ce mois-ci"
        :value="currentMonthDeleted"
        icon="fa-calendar-check"
      />
    </div>

    <!-- Cross-server queue table -->
    <div class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden">
      <div class="px-5 py-4 border-b border-[var(--border)] flex items-center justify-between">
        <h2 class="font-semibold text-sm">File d'attente — tous les serveurs</h2>
        <router-link to="/queue" class="text-xs text-[var(--accent)] hover:underline">Voir tout →</router-link>
      </div>
      <div class="p-4 text-[var(--muted)] text-sm text-center" v-if="loading">
        Chargement...
      </div>
      <div v-else>
        <MediaTable :items="recentQueue" :show-server-dot="true" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useStatsStore } from '@/stores/stats'
import StatCard  from '@/components/ui/StatCard.vue'
import MediaTable from '@/components/media/MediaTable.vue'
import api from '@/api/client'

const stats   = useStatsStore()
const loading = ref(false)
const recentQueue = ref([])

const currentMonthDeleted = computed(() => {
  const now = new Date()
  const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
  const entry = (stats.global.by_month || []).find(m => m.month === month)
  return entry?.total_deleted ?? 0
})

onMounted(async () => {
  loading.value = true
  try {
    await Promise.all([stats.fetchGlobal(), stats.fetchStorage()])
    const { data } = await api.get('/media?status=pending&limit=10')
    recentQueue.value = data?.items || data || []
  } finally {
    loading.value = false
  }
})
</script>
```

- [ ] **Step 6: Create stub `frontend/vue/src/components/media/MediaTable.vue`**

```vue
<!-- frontend/vue/src/components/media/MediaTable.vue -->
<template>
  <div v-if="!items.length" class="py-8 text-center text-[var(--muted)] text-sm">
    Aucun élément.
  </div>
  <table v-else class="w-full text-sm">
    <thead>
      <tr class="text-xs text-[var(--muted)] border-b border-[var(--border)]">
        <th v-if="showServerDot" class="w-2 px-4 py-2" />
        <th class="text-left px-4 py-2">Titre</th>
        <th class="text-left px-4 py-2">Bibliothèque</th>
        <th class="text-left px-4 py-2">Statut</th>
        <th class="text-left px-4 py-2">Suppression le</th>
      </tr>
    </thead>
    <tbody>
      <tr
        v-for="item in items"
        :key="item.id"
        class="border-b border-[var(--border)] hover:bg-[var(--bg3)] transition-colors"
      >
        <td v-if="showServerDot" class="px-4 py-2">
          <span class="w-2 h-2 rounded-full inline-block" :class="serverDot(item)" />
        </td>
        <td class="px-4 py-2 font-medium truncate max-w-[200px]">{{ item.title }}</td>
        <td class="px-4 py-2 text-[var(--muted)]">{{ item.library_name }}</td>
        <td class="px-4 py-2">
          <span class="px-2 py-0.5 rounded text-xs" :class="statusClass(item.status)">
            {{ item.status }}
          </span>
        </td>
        <td class="px-4 py-2 text-[var(--muted)]">{{ formatDate(item.delete_at) }}</td>
      </tr>
    </tbody>
  </table>
</template>

<script setup>
defineProps({
  items:         { type: Array, default: () => [] },
  showServerDot: { type: Boolean, default: false },
})

function serverDot(item) {
  return 'bg-indigo-400'  // will be enhanced when server type is available
}

function statusClass(status) {
  return {
    pending:  'bg-yellow-500/20 text-yellow-400',
    deleted:  'bg-red-500/20 text-red-400',
    error:    'bg-red-700/20 text-red-300',
  }[status] || 'bg-[var(--bg3)] text-[var(--muted)]'
}

function formatDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric' })
}
</script>
```

- [ ] **Step 7: Build and verify**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | tail -5
```
Expected: `✓ built`

- [ ] **Step 8: Commit**

```bash
cd /opt/claude/hygie && git add frontend/vue/src/
git commit -m "feat(frontend): Setup, Login, Dashboard views + StatCard + MediaTable stubs"
```

---

### Task 5: Remaining views (Queue, Rules stub, Settings, Logs)

**Files:**
- Create: `frontend/vue/src/views/QueueView.vue`
- Create: `frontend/vue/src/views/RulesView.vue` (stub — full rule builder is Phase 4)
- Create: `frontend/vue/src/views/SettingsView.vue`
- Create: `frontend/vue/src/views/LogsView.vue`
- Create: `frontend/vue/src/views/LibraryView.vue`
- Create: `frontend/vue/src/components/ui/ToggleSlider.vue`

- [ ] **Step 1: Create `frontend/vue/src/components/ui/ToggleSlider.vue`**

```vue
<!-- frontend/vue/src/components/ui/ToggleSlider.vue -->
<template>
  <label class="relative inline-flex items-center cursor-pointer select-none" :class="{ 'opacity-50 cursor-not-allowed': disabled }">
    <input type="checkbox" class="sr-only peer" v-model="checked" :disabled="disabled" @change="$emit('update:modelValue', checked)" />
    <div class="w-10 h-5 rounded-full transition-colors peer-checked:bg-green-500 bg-red-500/70 peer-focus:ring-2 peer-focus:ring-[var(--accent)]" />
    <div class="absolute left-0.5 top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform peer-checked:translate-x-5" />
  </label>
</template>

<script setup>
import { ref, watch } from 'vue'
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  disabled:   { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])
const checked = ref(props.modelValue)
watch(() => props.modelValue, v => { checked.value = v })
</script>
```

- [ ] **Step 2: Create `frontend/vue/src/views/QueueView.vue`**

```vue
<!-- frontend/vue/src/views/QueueView.vue -->
<template>
  <div class="space-y-4">
    <div class="flex items-center gap-4 flex-wrap">
      <select v-model="statusFilter" class="bg-[var(--bg2)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm">
        <option value="">Tous les statuts</option>
        <option value="pending">En attente</option>
        <option value="deleted">Supprimés</option>
        <option value="error">Erreur</option>
      </select>
      <input
        v-model="search"
        placeholder="Rechercher..."
        class="bg-[var(--bg2)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm w-64 focus:outline-none focus:border-[var(--accent)]"
      />
      <span class="text-sm text-[var(--muted)] ml-auto">{{ total }} éléments</span>
    </div>

    <div class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden">
      <MediaTable :items="items" />
    </div>

    <!-- Pagination -->
    <div v-if="totalPages > 1" class="flex items-center justify-center gap-2">
      <button
        v-for="p in totalPages"
        :key="p"
        @click="page = p"
        class="w-8 h-8 rounded text-sm transition-colors"
        :class="p === page ? 'bg-[var(--accent)] text-white' : 'text-[var(--muted)] hover:bg-[var(--bg3)]'"
      >{{ p }}</button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import api from '@/api/client'
import MediaTable from '@/components/media/MediaTable.vue'

const items        = ref([])
const total        = ref(0)
const page         = ref(1)
const statusFilter = ref('')
const search       = ref('')
const perPage      = 50

const totalPages = computed(() => Math.ceil(total.value / perPage))

async function load() {
  const params = { limit: perPage, offset: (page.value - 1) * perPage }
  if (statusFilter.value) params.status = statusFilter.value
  if (search.value)        params.search = search.value
  const { data } = await api.get('/media', { params })
  items.value = data.items || data || []
  total.value = data.total || items.value.length
}

watch([statusFilter, search], () => { page.value = 1; load() })
watch(page, load)
onMounted(load)
</script>
```

- [ ] **Step 3: Create `frontend/vue/src/views/RulesView.vue` (stub for Phase 4)**

```vue
<!-- frontend/vue/src/views/RulesView.vue -->
<!-- Rule builder is implemented in Phase 4. This is a placeholder. -->
<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <h2 class="font-semibold">Règles</h2>
      <button
        class="bg-[var(--accent)] hover:opacity-90 rounded-lg px-4 py-2 text-sm font-medium transition-opacity"
        @click="showCreate = true"
      >
        + Nouvelle règle
      </button>
    </div>
    <div class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-8 text-center text-[var(--muted)] text-sm">
      Le gestionnaire de règles visuelles arrive en Phase 4.
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
const showCreate = ref(false)
</script>
```

- [ ] **Step 4: Create `frontend/vue/src/views/LogsView.vue`**

```vue
<!-- frontend/vue/src/views/LogsView.vue -->
<template>
  <div class="space-y-4">
    <div class="flex gap-3">
      <select v-model="level" class="bg-[var(--bg2)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm">
        <option value="">Tous niveaux</option>
        <option value="DEBUG">DEBUG</option>
        <option value="INFO">INFO</option>
        <option value="WARNING">WARNING</option>
        <option value="ERROR">ERROR</option>
      </select>
    </div>

    <div class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden font-mono text-xs">
      <div v-if="!logs.length" class="p-8 text-center text-[var(--muted)]">Aucun log.</div>
      <div
        v-for="log in logs"
        :key="log.id"
        class="flex gap-3 px-4 py-1.5 border-b border-[var(--border)] hover:bg-[var(--bg3)] transition-colors"
        :class="levelClass(log.level)"
      >
        <span class="text-[var(--muted)] shrink-0 w-40">{{ formatTs(log.ts) }}</span>
        <span class="font-semibold shrink-0 w-16">{{ log.level }}</span>
        <span class="truncate">{{ log.message }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import api from '@/api/client'

const logs  = ref([])
const level = ref('')

async function load() {
  const params = { limit: 200 }
  if (level.value) params.level = level.value
  const { data } = await api.get('/logs', { params })
  logs.value = data.logs || data || []
}

function levelClass(l) {
  return { ERROR: 'text-red-400', WARNING: 'text-yellow-400' }[l] || ''
}
function formatTs(ts) {
  return ts ? new Date(ts).toLocaleString('fr-FR') : ''
}

watch(level, load)
onMounted(load)
</script>
```

- [ ] **Step 5: Create `frontend/vue/src/views/LibraryView.vue`**

```vue
<!-- frontend/vue/src/views/LibraryView.vue -->
<template>
  <div class="space-y-6" v-if="library">
    <div class="flex items-center gap-4">
      <h2 class="font-bold text-xl">{{ library.name }}</h2>
      <span class="text-xs text-[var(--muted)] bg-[var(--bg3)] px-2 py-1 rounded">{{ library.deletion_unit }}</span>
    </div>
    <div class="grid grid-cols-2 xl:grid-cols-4 gap-4">
      <StatCard label="En attente" :value="queueCount" icon="fa-clock" />
      <StatCard label="Supprimés" :value="deletedCount" icon="fa-trash" />
    </div>
    <div class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden">
      <MediaTable :items="items" />
    </div>
  </div>
  <div v-else class="text-[var(--muted)] text-sm p-8">Bibliothèque introuvable.</div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useServersStore } from '@/stores/servers'
import api from '@/api/client'
import StatCard   from '@/components/ui/StatCard.vue'
import MediaTable from '@/components/media/MediaTable.vue'

const route   = useRoute()
const servers = useServersStore()
const items   = ref([])

const library = computed(() => servers.libraries.find(l => l.id === route.params.id))
const queueCount   = computed(() => items.value.filter(i => i.status === 'pending').length)
const deletedCount = computed(() => items.value.filter(i => i.status === 'deleted').length)

onMounted(async () => {
  if (!servers.libraries.length) await servers.fetch()
  const { data } = await api.get('/media', { params: { library_id: route.params.id, limit: 200 } })
  items.value = data.items || data || []
})
</script>
```

- [ ] **Step 6: Create `frontend/vue/src/views/SettingsView.vue`**

```vue
<!-- frontend/vue/src/views/SettingsView.vue -->
<template>
  <div class="max-w-2xl space-y-8">
    <div v-if="saved" class="bg-green-500/20 border border-green-500/30 text-green-400 rounded-lg px-4 py-3 text-sm">
      Paramètres sauvegardés.
    </div>

    <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-5">
      <h2 class="font-semibold">Général</h2>

      <div class="flex items-center justify-between">
        <div>
          <div class="text-sm font-medium">Mode Dry Run</div>
          <div class="text-xs text-[var(--muted)]">Simule les suppressions sans les exécuter</div>
        </div>
        <ToggleSlider v-model="form.dry_run" />
      </div>

      <div class="flex items-center justify-between">
        <div>
          <div class="text-sm font-medium">Sauvegarde automatique</div>
          <div class="text-xs text-[var(--muted)]">Sauvegarde la base de données avant suppression</div>
        </div>
        <ToggleSlider v-model="form.backup_enabled" />
      </div>
    </section>

    <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-4">
      <h2 class="font-semibold">Intervalles</h2>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Scan (minutes)</label>
          <input v-model.number="form.scan_interval_minutes" type="number" min="10"
            class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Suppression (minutes)</label>
          <input v-model.number="form.deletion_check_interval_minutes" type="number" min="10"
            class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm" />
        </div>
      </div>
    </section>

    <button
      @click="save"
      :disabled="saving"
      class="bg-[var(--accent)] hover:opacity-90 disabled:opacity-50 rounded-lg px-6 py-2.5 text-sm font-semibold transition-opacity"
    >
      {{ saving ? 'Enregistrement...' : 'Enregistrer' }}
    </button>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { useSettingsStore } from '@/stores/settings'
import ToggleSlider from '@/components/ui/ToggleSlider.vue'

const settings = useSettingsStore()
const form   = ref({})
const saving = ref(false)
const saved  = ref(false)

function syncForm() {
  form.value = {
    dry_run:                         settings.settings.dry_run === 'true' || settings.settings.dry_run === true,
    backup_enabled:                  settings.settings.backup_enabled === 'true' || settings.settings.backup_enabled === true,
    scan_interval_minutes:           Number(settings.settings.scan_interval_minutes || 360),
    deletion_check_interval_minutes: Number(settings.settings.deletion_check_interval_minutes || 60),
  }
}

watch(() => settings.settings, syncForm, { deep: true })

async function save() {
  saving.value = true
  saved.value  = false
  try {
    await settings.save({
      dry_run:                         String(form.value.dry_run),
      backup_enabled:                  String(form.value.backup_enabled),
      scan_interval_minutes:           String(form.value.scan_interval_minutes),
      deletion_check_interval_minutes: String(form.value.deletion_check_interval_minutes),
    })
    saved.value = true
    setTimeout(() => { saved.value = false }, 3000)
  } finally {
    saving.value = false
  }
}

onMounted(async () => { await settings.fetch(); syncForm() })
</script>
```

- [ ] **Step 7: Build final**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | tail -5
```
Expected: `✓ built`

- [ ] **Step 8: Commit**

```bash
cd /opt/claude/hygie && git add frontend/vue/src/
git commit -m "feat(frontend): Queue, Logs, Library, Settings views + ToggleSlider component"
```

---

### Task 6: Wire backend to serve Vue dist + update Dockerfile

**Files:**
- Modify: `backend/main.py` — serve `frontend/dist` instead of `frontend/templates`
- Modify: `Dockerfile` — multi-stage: Node build then Python

- [ ] **Step 1: Update `backend/main.py` static file serving**

Find the `StaticFiles` mount for frontend templates and replace with:

```python
import os as _os
from fastapi.responses import FileResponse

_DIST = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "frontend", "dist")
_LEGACY = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "frontend", "templates")

# Serve Vue dist assets
if _os.path.isdir(_DIST):
    app.mount("/assets", StaticFiles(directory=_os.path.join(_DIST, "assets")), name="vue-assets")

# SPA fallback: serve index.html for all unknown routes
@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    if _os.path.isdir(_DIST):
        return FileResponse(_os.path.join(_DIST, "index.html"))
    # Fallback to legacy Jinja2 during development
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory=_LEGACY)
    from fastapi import Request
    # (return old template response — keep existing route intact)
    return FileResponse(_os.path.join(_LEGACY, "index.html"))
```

- [ ] **Step 2: Update Dockerfile — multi-stage with Node build**

```dockerfile
# ── Stage 1: Build Vue frontend ──────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /build
COPY frontend/vue/package*.json ./
RUN npm ci --prefer-offline
COPY frontend/vue/ ./
RUN npm run build

# ── Stage 2: Python backend ───────────────────────────────────────────────────
FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/static/ ./frontend/static/
COPY frontend/templates/ ./frontend/templates/

# Copy Vue dist from builder stage
COPY --from=frontend-builder /build/../dist/ ./frontend/dist/

ENV PYTHONUNBUFFERED=1 DB_PATH=/app/data/hygie.db
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Build Docker image to verify**

```bash
cd /opt/claude/hygie && docker build -t hygie:v3-frontend-test .
docker run --rm -p 8001:8000 hygie:v3-frontend-test &
sleep 3 && curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/ && kill %1
```
Expected: `200`

- [ ] **Step 4: Commit**

```bash
git add backend/main.py Dockerfile
git commit -m "feat(frontend): serve Vue 3 dist from FastAPI, multi-stage Dockerfile"
```

---

### Task 7: Version bump + tag v3.0.0-alpha.3

- [ ] **Step 1: Bump version**

```python
VERSION = "3.0.0-alpha.3"
```

- [ ] **Step 2: Full suite + Docker build**

```bash
cd /opt/claude/hygie && python -m pytest tests/ -q 2>&1 | tail -5
docker build -t hygie:3.0.0-alpha.3 .
```

- [ ] **Step 3: Tag and push**

```bash
git add backend/version.py
git commit -m "chore: bump version to 3.0.0-alpha.3 (Vue 3 frontend phase)"
git tag v3.0.0-alpha.3
git push origin main --tags
```
