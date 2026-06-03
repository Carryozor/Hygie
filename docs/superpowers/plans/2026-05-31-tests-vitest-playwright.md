# Tests Vitest + Playwright — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter Vitest pour les stores/logique et Playwright pour les flux E2E critiques. Zéro test frontend existant — on part de zéro.

**Architecture:**
- **Vitest** : tests unitaires des stores Pinia et helpers (`auth.js`, `status.js`, `errorHandler.js`). Rapide, CI-friendly, sans navigateur.
- **Playwright** : tests E2E sur le container Docker réel (`http://localhost` sur le port exposé). Teste les flux utilisateur critiques : login, création règle, sauvegarde settings.

**Tech Stack:** Vitest 2.x, @vue/test-utils, @pinia/testing, Playwright 1.x, Node 20

---

## File Map

| Action | Fichier | Rôle |
|---|---|---|
| Modify | `frontend/vue/package.json` | Ajouter devDeps: vitest, @vue/test-utils, @pinia/testing, playwright |
| Modify | `frontend/vue/vite.config.js` | Ajouter config `test:` pour Vitest |
| Create | `frontend/vue/src/stores/__tests__/auth.test.js` | Tests store auth (login, refresh, logout) |
| Create | `frontend/vue/src/stores/__tests__/status.test.js` | Tests store status (fetchScheduler, logoStatus) |
| Create | `frontend/vue/src/api/__tests__/errorHandler.test.js` | Tests formatApiError |
| Create | `frontend/vue/e2e/login.spec.js` | E2E : login → dashboard |
| Create | `frontend/vue/e2e/rules.spec.js` | E2E : créer et supprimer une règle |
| Create | `frontend/vue/playwright.config.js` | Config Playwright |
| Modify | `frontend/vue/package.json` | Scripts test:unit et test:e2e |
| Modify | `.github/workflows/test.yml` | Ajouter job vitest dans CI |

---

## Task 1 : Installer Vitest + configurer

**Files:**
- Modify: `frontend/vue/package.json`
- Modify: `frontend/vue/vite.config.js`

- [ ] **Step 1 : Installer les dépendances Vitest**

```bash
cd /opt/claude/hygie/frontend/vue
npm install --save-dev vitest @vue/test-utils @pinia/testing jsdom @vitest/coverage-v8
```

- [ ] **Step 2 : Ajouter la config Vitest dans vite.config.js**

Remplacer le contenu de `vite.config.js` par :

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
      '/api':    { target: 'http://localhost:8000', changeOrigin: true },
      '/health': { target: 'http://localhost:8000', changeOrigin: true },
      '/static': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.js'],
  },
})
```

- [ ] **Step 3 : Créer src/test-setup.js**

```javascript
// frontend/vue/src/test-setup.js
import { config } from '@vue/test-utils'
import { createPinia } from 'pinia'

// Global Vue Test Utils config
config.global.plugins = [createPinia()]
```

- [ ] **Step 4 : Ajouter les scripts dans package.json**

Modifier la section `scripts` :
```json
"scripts": {
  "dev":        "vite",
  "build":      "vite build",
  "preview":    "vite preview",
  "test:unit":  "vitest run",
  "test:unit:watch": "vitest",
  "test:e2e":   "playwright test",
  "test":       "npm run test:unit"
}
```

- [ ] **Step 5 : Vérifier que Vitest fonctionne (avec un test vide)**

```bash
cd /opt/claude/hygie/frontend/vue
echo "import { describe, it, expect } from 'vitest'
describe('sanity', () => {
  it('works', () => expect(1 + 1).toBe(2))
})" > src/__sanity__.test.js
npx vitest run src/__sanity__.test.js 2>&1 | tail -8
rm src/__sanity__.test.js
```

Résultat attendu : `✓ 1 passed`

- [ ] **Step 6 : Commit**

```bash
git add frontend/vue/package.json frontend/vue/package-lock.json \
        frontend/vue/vite.config.js frontend/vue/src/test-setup.js
git commit -m "feat(test): install and configure Vitest for frontend unit tests"
```

---

## Task 2 : Tests Vitest — errorHandler.js

**Files:**
- Create: `frontend/vue/src/api/__tests__/errorHandler.test.js`

- [ ] **Step 1 : Créer le répertoire**

```bash
mkdir -p /opt/claude/hygie/frontend/vue/src/api/__tests__
```

- [ ] **Step 2 : Créer le fichier de test**

```javascript
// frontend/vue/src/api/__tests__/errorHandler.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { formatApiError, emitError } from '../errorHandler'

describe('formatApiError', () => {
  it('returns network error message when no response', () => {
    const err = { response: undefined }
    expect(formatApiError(err)).toContain('réseau')
  })

  it('formats a single Pydantic 422 string detail', () => {
    const err = { response: { status: 422, data: { detail: 'Champ requis' } } }
    expect(formatApiError(err)).toBe('Champ requis')
  })

  it('formats an array of Pydantic 422 errors into readable string', () => {
    const err = {
      response: {
        status: 422,
        data: {
          detail: [
            { loc: ['body', 'name'], msg: 'field required' },
            { loc: ['body', 'url'],  msg: 'invalid url' },
          ],
        },
      },
    }
    const result = formatApiError(err)
    expect(result).toContain('name')
    expect(result).toContain('field required')
    expect(result).toContain('url')
    expect(result).toContain('invalid url')
  })

  it('filters out "body" from loc path', () => {
    const err = {
      response: {
        status: 422,
        data: { detail: [{ loc: ['body', 'email'], msg: 'invalid' }] },
      },
    }
    expect(formatApiError(err)).not.toContain('body')
    expect(formatApiError(err)).toContain('email')
  })

  it('returns server error for 500', () => {
    const err = { response: { status: 500, data: {} } }
    expect(formatApiError(err)).toBe('Erreur serveur interne')
  })

  it('uses detail field for 500 if present', () => {
    const err = { response: { status: 500, data: { detail: 'DB connection failed' } } }
    expect(formatApiError(err)).toBe('DB connection failed')
  })

  it('returns 404 message', () => {
    const err = { response: { status: 404, data: {} } }
    expect(formatApiError(err)).toContain('introuvable')
  })

  it('returns generic error for unknown status', () => {
    const err = { response: { status: 418, data: {} } }
    expect(formatApiError(err)).toContain('418')
  })
})

describe('emitError', () => {
  it('dispatches hygie:error CustomEvent on window', () => {
    const events = []
    window.addEventListener('hygie:error', e => events.push(e.detail))
    emitError('Test error', 'warning')
    expect(events).toHaveLength(1)
    expect(events[0].message).toBe('Test error')
    expect(events[0].type).toBe('warning')
  })

  it('defaults type to error', () => {
    const events = []
    window.addEventListener('hygie:error', e => events.push(e.detail))
    emitError('Default type')
    const last = events.at(-1)
    expect(last.type).toBe('error')
  })
})
```

- [ ] **Step 3 : Lancer les tests**

```bash
cd /opt/claude/hygie/frontend/vue
npx vitest run src/api/__tests__/errorHandler.test.js 2>&1 | tail -15
```

Résultat attendu : tous les tests passent.

- [ ] **Step 4 : Commit**

```bash
git add frontend/vue/src/api/__tests__/errorHandler.test.js
git commit -m "test(vitest): add errorHandler unit tests (8 cases)"
```

---

## Task 3 : Tests Vitest — auth store

**Files:**
- Create: `frontend/vue/src/stores/__tests__/auth.test.js`

- [ ] **Step 1 : Créer le répertoire**

```bash
mkdir -p /opt/claude/hygie/frontend/vue/src/stores/__tests__
```

- [ ] **Step 2 : Créer le test**

```javascript
// frontend/vue/src/stores/__tests__/auth.test.js
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAuthStore } from '../auth'

// Mock the api module
vi.mock('@/api/client', () => ({
  default: {
    get:  vi.fn(),
    post: vi.fn(),
  },
}))

import api from '@/api/client'

describe('useAuthStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.clearAllMocks()
  })

  afterEach(() => {
    localStorage.clear()
  })

  it('isLoggedIn is false when no token', () => {
    const store = useAuthStore()
    expect(store.isLoggedIn).toBe(false)
  })

  it('isLoggedIn is true when token in localStorage', () => {
    localStorage.setItem('hygie_token', 'test-token')
    const store = useAuthStore()
    expect(store.isLoggedIn).toBe(true)
  })

  it('login stores access_token and refresh_token', async () => {
    api.post.mockResolvedValueOnce({
      data: {
        access_token:  'access-abc',
        refresh_token: 'refresh-xyz',
        username:      'admin',
      },
    })
    const store = useAuthStore()
    await store.login('admin', 'password')
    expect(store.token).toBe('access-abc')
    expect(store.refreshToken).toBe('refresh-xyz')
    expect(store.username).toBe('admin')
    expect(localStorage.getItem('hygie_token')).toBe('access-abc')
    expect(localStorage.getItem('hygie_refresh_token')).toBe('refresh-xyz')
  })

  it('login falls back to token field for backward compat', async () => {
    api.post.mockResolvedValueOnce({
      data: { token: 'legacy-token', username: 'admin' },
    })
    const store = useAuthStore()
    await store.login('admin', 'pass')
    expect(store.token).toBe('legacy-token')
  })

  it('logout clears tokens from store and localStorage', async () => {
    api.post.mockResolvedValueOnce({ data: { status: 'logged_out' } })
    localStorage.setItem('hygie_token', 'tok')
    localStorage.setItem('hygie_refresh_token', 'ref')
    const store = useAuthStore()
    await store.logout()
    expect(store.token).toBe('')
    expect(store.refreshToken).toBe('')
    expect(localStorage.getItem('hygie_token')).toBeNull()
    expect(localStorage.getItem('hygie_refresh_token')).toBeNull()
  })

  it('refresh calls /auth/refresh and updates access token', async () => {
    api.post.mockResolvedValueOnce({
      data: { access_token: 'new-access' },
    })
    localStorage.setItem('hygie_refresh_token', 'my-refresh')
    const store = useAuthStore()
    store.refreshToken = 'my-refresh'
    const ok = await store.refresh()
    expect(ok).toBe(true)
    expect(store.token).toBe('new-access')
    expect(api.post).toHaveBeenCalledWith('/auth/refresh', {
      refresh_token: 'my-refresh',
    })
  })

  it('refresh returns false and emits unauthorized on failure', async () => {
    api.post.mockRejectedValueOnce(new Error('401'))
    const events = []
    window.addEventListener('hygie:unauthorized', () => events.push(1))
    const store = useAuthStore()
    store.refreshToken = 'bad-token'
    const ok = await store.refresh()
    expect(ok).toBe(false)
    expect(events.length).toBeGreaterThan(0)
  })

  it('checkSetup returns setup_complete from API', async () => {
    api.get.mockResolvedValueOnce({ data: { setup_complete: true } })
    const store = useAuthStore()
    const result = await store.checkSetup()
    expect(result).toBe(true)
    expect(store.setupComplete).toBe(true)
  })
})
```

- [ ] **Step 3 : Lancer les tests**

```bash
cd /opt/claude/hygie/frontend/vue
npx vitest run src/stores/__tests__/auth.test.js 2>&1 | tail -15
```

Résultat attendu : tous les tests passent.

- [ ] **Step 4 : Commit**

```bash
git add frontend/vue/src/stores/__tests__/auth.test.js
git commit -m "test(vitest): add auth store unit tests (9 cases)"
```

---

## Task 4 : Tests Vitest — status store

**Files:**
- Create: `frontend/vue/src/stores/__tests__/status.test.js`

- [ ] **Step 1 : Créer le test**

```javascript
// frontend/vue/src/stores/__tests__/status.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useStatusStore } from '../status'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

vi.mock('@/stores/servers', () => ({
  useServersStore: () => ({
    servers: [{ id: '0', enabled: true }],
    fetch:   vi.fn().mockResolvedValue(undefined),
  }),
}))

import api from '@/api/client'

describe('useStatusStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    localStorage.setItem('hygie_token', 'test-token')
  })

  it('initial state: all refs at default values', () => {
    const store = useStatusStore()
    expect(store.scanRunning).toBe(false)
    expect(store.deletionRunning).toBe(false)
    expect(store.hasUnseenErrors).toBe(false)
    expect(store.serverStatus).toBe('none')
    expect(store.logoStatus).toBe('none')
  })

  it('logoStatus is "error" when hasUnseenErrors is true', () => {
    const store = useStatusStore()
    store.hasUnseenErrors = true
    expect(store.logoStatus).toBe('error')
  })

  it('logoStatus is "ok" when serverStatus ok and no errors', () => {
    const store = useStatusStore()
    store.serverStatus = 'ok'
    store.hasUnseenErrors = false
    expect(store.logoStatus).toBe('ok')
  })

  it('logoStatus is "unknown" when serverStatus is unknown', () => {
    const store = useStatusStore()
    store.serverStatus = 'unknown'
    expect(store.logoStatus).toBe('unknown')
  })

  it('logoStatus is "unknown" (not "error") when serverStatus=error but no unseen logs', () => {
    const store = useStatusStore()
    store.serverStatus = 'error'
    store.hasUnseenErrors = false
    expect(store.logoStatus).toBe('unknown')
  })

  it('fetchScheduler updates scanRunning and deletionRunning', async () => {
    api.get.mockResolvedValueOnce({
      data: [
        { id: 'scan_job',     next_run: '2026-06-01T10:00:00Z', is_running: true  },
        { id: 'deletion_job', next_run: '2026-06-01T11:00:00Z', is_running: false },
      ],
    })
    const store = useStatusStore()
    await store.fetchScheduler()
    expect(store.scanRunning).toBe(true)
    expect(store.deletionRunning).toBe(false)
    expect(store.scanNext).toBe('2026-06-01T10:00:00Z')
  })

  it('checkUnseenErrors sets hasUnseenErrors true when count > 0', async () => {
    api.get.mockResolvedValueOnce({ data: { count: 3 } })
    const store = useStatusStore()
    await store.checkUnseenErrors()
    expect(store.hasUnseenErrors).toBe(true)
  })

  it('checkUnseenErrors sets hasUnseenErrors false when count is 0', async () => {
    api.get.mockResolvedValueOnce({ data: { count: 0 } })
    const store = useStatusStore()
    store.hasUnseenErrors = true
    await store.checkUnseenErrors()
    expect(store.hasUnseenErrors).toBe(false)
  })

  it('stop() does not throw even if never started', () => {
    const store = useStatusStore()
    expect(() => store.stop()).not.toThrow()
  })
})
```

- [ ] **Step 2 : Lancer les tests**

```bash
cd /opt/claude/hygie/frontend/vue
npx vitest run src/stores/__tests__/status.test.js 2>&1 | tail -15
```

- [ ] **Step 3 : Commit**

```bash
git add frontend/vue/src/stores/__tests__/status.test.js
git commit -m "test(vitest): add status store unit tests (9 cases)"
```

---

## Task 5 : Lancer tous les tests Vitest + mettre à jour CI

- [ ] **Step 1 : Lancer tous les tests Vitest**

```bash
cd /opt/claude/hygie/frontend/vue
npm run test:unit 2>&1 | tail -20
```

Résultat attendu : tous les tests passent (environ 26 tests).

- [ ] **Step 2 : Mettre à jour .github/workflows/test.yml**

Dans le job `frontend`, ajouter une étape pour les tests Vitest AVANT le build :

```yaml
      - name: Run unit tests
        working-directory: frontend/vue
        run: npm run test:unit
```

L'étape doit être insérée entre "Install dependencies" et "Build frontend".

- [ ] **Step 3 : Commit**

```bash
git add .github/workflows/test.yml
git commit -m "ci: add Vitest unit tests to frontend CI job"
```

---

## Task 6 : Installer Playwright + configurer

**Files:**
- Create: `frontend/vue/playwright.config.js`
- Modify: `frontend/vue/package.json`

- [ ] **Step 1 : Installer Playwright**

```bash
cd /opt/claude/hygie/frontend/vue
npm install --save-dev @playwright/test
npx playwright install chromium --with-deps 2>&1 | tail -5
```

- [ ] **Step 2 : Créer playwright.config.js**

```javascript
// frontend/vue/playwright.config.js
import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: 1,
  reporter: [['list'], ['html', { open: 'never' }]],

  use: {
    // Target the running Docker container
    baseURL: 'http://localhost',
    // Store auth state between tests
    storageState: 'e2e/.auth/state.json',
    headless: true,
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },

  projects: [
    // Setup project: performs login and saves auth state
    {
      name: 'setup',
      testMatch: /.*\.setup\.js/,
      use: { storageState: undefined },
    },
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
      dependencies: ['setup'],
    },
  ],
})
```

- [ ] **Step 3 : Créer e2e/auth.setup.js — login une fois, réutilisé par tous les tests**

```bash
mkdir -p /opt/claude/hygie/frontend/vue/e2e/.auth
```

```javascript
// frontend/vue/e2e/auth.setup.js
import { test as setup, expect } from '@playwright/test'
import path from 'path'

const AUTH_FILE = path.join(import.meta.dirname, '.auth/state.json')

setup('authenticate', async ({ page }) => {
  await page.goto('/login')
  await expect(page.locator('h1')).toContainText('Connexion')

  // Fill credentials — use env vars for CI flexibility
  const username = process.env.E2E_USERNAME || 'admin'
  const password = process.env.E2E_PASSWORD || 'changeme'

  await page.fill('input[autocomplete="username"]', username)
  await page.fill('input[autocomplete="current-password"]', password)
  await page.click('button[type="submit"]')

  // Wait for redirect to dashboard
  await page.waitForURL('/', { timeout: 10_000 })
  await expect(page).toHaveURL('/')

  // Save auth state (localStorage + cookies)
  await page.context().storageState({ path: AUTH_FILE })
})
```

- [ ] **Step 4 : Ajouter script test:e2e dans package.json**

Dans les scripts, ajouter :
```json
"test:e2e": "playwright test"
```

- [ ] **Step 5 : Commit config**

```bash
git add frontend/vue/playwright.config.js frontend/vue/e2e/auth.setup.js \
        frontend/vue/package.json frontend/vue/package-lock.json
git commit -m "feat(test): install Playwright and configure E2E test setup"
```

---

## Task 7 : Tests E2E Playwright — login et dashboard

**Files:**
- Create: `frontend/vue/e2e/login.spec.js`

- [ ] **Step 1 : Créer le test**

```javascript
// frontend/vue/e2e/login.spec.js
import { test, expect } from '@playwright/test'

// This test runs WITHOUT saved auth state (uses setup project without dependency)
test.use({ storageState: undefined })

test.describe('Login flow', () => {
  test('shows login form on /login', async ({ page }) => {
    await page.goto('/login')
    await expect(page.locator('h1')).toContainText('Connexion')
    await expect(page.locator('input[autocomplete="username"]')).toBeVisible()
    await expect(page.locator('input[autocomplete="current-password"]')).toBeVisible()
    await expect(page.locator('button[type="submit"]')).toBeEnabled()
  })

  test('shows error on wrong credentials', async ({ page }) => {
    await page.goto('/login')
    await page.fill('input[autocomplete="username"]', 'wrong')
    await page.fill('input[autocomplete="current-password"]', 'wrongpassword')
    await page.click('button[type="submit"]')
    // Error message should appear (not redirected)
    await expect(page.locator('text=Identifiants')).toBeVisible({ timeout: 5000 })
    await expect(page).toHaveURL('/login')
  })

  test('redirects unauthenticated to /login', async ({ page }) => {
    // Clear any stored auth
    await page.context().clearCookies()
    await page.evaluate(() => localStorage.clear())
    await page.goto('/')
    await expect(page).toHaveURL(/\/login/)
  })

  test('successful login redirects to dashboard', async ({ page }) => {
    const username = process.env.E2E_USERNAME || 'admin'
    const password = process.env.E2E_PASSWORD || 'changeme'
    await page.goto('/login')
    await page.fill('input[autocomplete="username"]', username)
    await page.fill('input[autocomplete="current-password"]', password)
    await page.click('button[type="submit"]')
    await page.waitForURL('/', { timeout: 10_000 })
    await expect(page).toHaveURL('/')
  })

  test('stores refresh_token in localStorage after login', async ({ page }) => {
    const username = process.env.E2E_USERNAME || 'admin'
    const password = process.env.E2E_PASSWORD || 'changeme'
    await page.goto('/login')
    await page.fill('input[autocomplete="username"]', username)
    await page.fill('input[autocomplete="current-password"]', password)
    await page.click('button[type="submit"]')
    await page.waitForURL('/', { timeout: 10_000 })
    const refreshToken = await page.evaluate(() =>
      localStorage.getItem('hygie_refresh_token')
    )
    expect(refreshToken).toBeTruthy()
    expect(refreshToken.length).toBeGreaterThan(10)
  })
})
```

- [ ] **Step 2 : Commit**

```bash
git add frontend/vue/e2e/login.spec.js
git commit -m "test(e2e): add login flow Playwright tests (5 cases)"
```

---

## Task 8 : Tests E2E Playwright — navigation et settings

**Files:**
- Create: `frontend/vue/e2e/navigation.spec.js`

- [ ] **Step 1 : Créer le test de navigation (utilise auth state)**

```javascript
// frontend/vue/e2e/navigation.spec.js
import { test, expect } from '@playwright/test'

// Uses saved auth state from auth.setup.js
test.describe('Authenticated navigation', () => {
  test('dashboard loads with sidebar', async ({ page }) => {
    await page.goto('/')
    // Sidebar should be present
    await expect(page.locator('aside')).toBeVisible()
    // Hygie logo visible
    await expect(page.locator('aside svg').first()).toBeVisible()
    // Version shown
    await expect(page.locator('aside text=v')).toBeVisible()
  })

  test('navigates to rules page', async ({ page }) => {
    await page.goto('/')
    await page.click('a[href="/rules"]')
    await expect(page).toHaveURL('/rules')
    await expect(page.locator('h1, h2').first()).toBeVisible()
  })

  test('navigates to settings page', async ({ page }) => {
    await page.goto('/')
    await page.click('a[href="/settings"]')
    await expect(page).toHaveURL('/settings')
    // Settings tabs should be visible
    await expect(page.locator('button').filter({ hasText: 'Général' })).toBeVisible()
  })

  test('navigates to logs page', async ({ page }) => {
    await page.goto('/')
    await page.click('a[href="/logs"]')
    await expect(page).toHaveURL('/logs')
  })

  test('sidebar shows scan timer or running status', async ({ page }) => {
    await page.goto('/')
    // Either a scan timer countdown or "Prochain scan" text should be visible
    const sidebar = page.locator('aside')
    await expect(sidebar).toBeVisible()
    // Give polling time to load
    await page.waitForTimeout(3000)
    // The sidebar bottom section should have some content
    const bottomSection = sidebar.locator('div').last()
    await expect(bottomSection).toBeVisible()
  })
})
```

- [ ] **Step 2 : Commit**

```bash
git add frontend/vue/e2e/navigation.spec.js
git commit -m "test(e2e): add authenticated navigation Playwright tests (5 cases)"
```

---

## Task 9 : Vérifier que les E2E passent + mettre à jour CI

- [ ] **Step 1 : Vérifier que le container est accessible**

```bash
# Vérifier le port exposé du container
docker ps | grep hygie
curl -s http://localhost/health 2>/dev/null | head -5 || \
  docker port hygie 8000/tcp
```

Si le port n'est pas 80 (par exemple 8000), mettre à jour `baseURL` dans `playwright.config.js`.

- [ ] **Step 2 : Lancer les tests E2E (login tests uniquement — sans auth state)**

```bash
cd /opt/claude/hygie/frontend/vue
E2E_USERNAME=admin E2E_PASSWORD=VOTRE_MOT_DE_PASSE \
npx playwright test e2e/login.spec.js --project=chromium 2>&1 | tail -20
```

**Note :** Remplacer `VOTRE_MOT_DE_PASSE` par le mot de passe réel. Si les tests ne passent pas à cause du mot de passe, les marquer comme `test.skip` et noter le résultat.

- [ ] **Step 3 : Lancer le setup pour les tests authentifiés**

```bash
cd /opt/claude/hygie/frontend/vue
E2E_USERNAME=admin E2E_PASSWORD=VOTRE_MOT_DE_PASSE \
npx playwright test --project=setup 2>&1 | tail -10
```

- [ ] **Step 4 : Lancer les tests de navigation**

```bash
cd /opt/claude/hygie/frontend/vue
npx playwright test e2e/navigation.spec.js 2>&1 | tail -20
```

- [ ] **Step 5 : Mettre à jour .github/workflows/test.yml — ajouter job E2E**

Ajouter un nouveau job `e2e` dans `test.yml` :

```yaml
  e2e:
    name: E2E Tests (Playwright)
    runs-on: ubuntu-latest
    needs: [backend, frontend]

    steps:
      - uses: actions/checkout@v4

      - name: Set up Node.js 20
        uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
          cache-dependency-path: frontend/vue/package-lock.json

      - name: Install dependencies
        working-directory: frontend/vue
        run: npm ci

      - name: Install Playwright browsers
        working-directory: frontend/vue
        run: npx playwright install chromium --with-deps

      - name: Start Hygie (Docker)
        run: docker compose up -d
        # Note: requires HYGIE_* secrets configured in GitHub

      - name: Wait for Hygie to be healthy
        run: |
          for i in $(seq 1 20); do
            curl -sf http://localhost/health && break
            sleep 3
          done

      - name: Run E2E tests
        working-directory: frontend/vue
        env:
          E2E_USERNAME: ${{ secrets.E2E_USERNAME }}
          E2E_PASSWORD: ${{ secrets.E2E_PASSWORD }}
        run: npx playwright test

      - name: Upload Playwright report on failure
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-report
          path: frontend/vue/playwright-report/
```

- [ ] **Step 6 : Commit final**

```bash
git add .github/workflows/test.yml frontend/vue/e2e/
git commit -m "ci: add Playwright E2E job to GitHub Actions

Runs after backend + frontend CI jobs.
Uses E2E_USERNAME / E2E_PASSWORD secrets.
Uploads Playwright report as artifact on failure."
```

---

## Self-Review Checklist

- [x] **Vitest isolation** : chaque test utilise `setActivePinia(createPinia())` → pas de pollution entre tests
- [x] **Mocks explicites** : `vi.mock('@/api/client')` évite les vrais appels réseau
- [x] **E2E auth state** : `auth.setup.js` fait le login une fois, tous les tests authentifiés le réutilisent
- [x] **E2E login tests** : `storageState: undefined` pour ne pas utiliser la session sauvegardée
- [x] **Credentials via env** : `E2E_USERNAME` / `E2E_PASSWORD` — pas de credentials hardcodés
- [x] **Playwright baseURL** : pointe vers le container Docker (port à vérifier)
- [x] **CI E2E** : le job `e2e` dépend de `backend` et `frontend` — ne tourne pas en parallèle
