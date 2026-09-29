// src/router/__tests__/index.cov.test.js
// Coverage for router/index.js's beforeEach navigation guard (17% before
// this file — only the route TABLE was exercised, never the guard that
// decides whether a navigation is allowed through).
//
// CLAUDE.md piège 3: auth state must be read via useAuthStore()/tokenStore,
// never localStorage — these tests drive the guard purely through the
// mocked auth store's reactive state, the same contract the real store
// exposes.
import { describe, it, expect, vi, beforeEach } from 'vitest'

const authState = vi.hoisted(() => ({
  setupComplete: null,
  isLoggedIn: false,
  triedSilentRefresh: false,
  checkSetup: vi.fn(),
  refresh: vi.fn(),
}))

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => authState,
}))

// Route components are heavy .vue SFCs — irrelevant to guard logic and slow
// to resolve under lazy import(); stub them all so navigation resolves fast.
vi.mock('@/views/SetupView.vue', () => ({ default: { render: () => null } }))
vi.mock('@/views/LoginView.vue', () => ({ default: { render: () => null } }))
vi.mock('@/views/DashboardView.vue', () => ({ default: { render: () => null } }))
vi.mock('@/views/LibraryView.vue', () => ({ default: { render: () => null } }))
vi.mock('@/views/QueueView.vue', () => ({ default: { render: () => null } }))
vi.mock('@/views/CalendarView.vue', () => ({ default: { render: () => null } }))
vi.mock('@/views/RulesView.vue', () => ({ default: { render: () => null } }))
vi.mock('@/views/SettingsView.vue', () => ({ default: { render: () => null } }))
vi.mock('@/views/LogsView.vue', () => ({ default: { render: () => null } }))
vi.mock('@/views/IgnoredView.vue', () => ({ default: { render: () => null } }))
vi.mock('@/views/PublicView.vue', () => ({ default: { render: () => null } }))

import router from '../index'
import { ROUTES } from '../paths'

function resetAuthState() {
  authState.setupComplete = null
  authState.isLoggedIn = false
  authState.triedSilentRefresh = false
  authState.checkSetup = vi.fn().mockResolvedValue(true)
  authState.refresh = vi.fn().mockResolvedValue(false)
}

describe('router navigation guard', () => {
  beforeEach(async () => {
    resetAuthState()
    // Return every navigation to a neutral starting point.
    await router.push('/login')
  })

  it('lets a public route through without touching the auth store at all', async () => {
    await router.push('/login')
    expect(router.currentRoute.value.name).toBe('login')
    expect(authState.checkSetup).not.toHaveBeenCalled()
  })

  it('lets the public calendar slug through without touching the auth store', async () => {
    await router.push('/my-calendar-slug')
    expect(router.currentRoute.value.name).toBe('public')
    expect(authState.checkSetup).not.toHaveBeenCalled()
  })

  it('redirects a protected route to /setup when setup is not complete', async () => {
    authState.checkSetup.mockResolvedValue(false)
    await router.push('/queue')
    expect(router.currentRoute.value.name).toBe('setup')
  })

  it('calls checkSetup only once per session — reuses the cached setupComplete afterwards', async () => {
    authState.setupComplete = true
    authState.isLoggedIn = true
    await router.push('/queue')
    expect(authState.checkSetup).not.toHaveBeenCalled()
  })

  it('also reuses a cached setupComplete=false without re-calling checkSetup', async () => {
    authState.setupComplete = false
    await router.push('/queue')
    expect(authState.checkSetup).not.toHaveBeenCalled()
    expect(router.currentRoute.value.name).toBe('setup')
  })

  it('attempts one silent refresh when not logged in and none was tried yet, then redirects to login on failure', async () => {
    authState.setupComplete = true
    authState.isLoggedIn = false
    authState.triedSilentRefresh = false
    authState.refresh.mockImplementation(async () => {
      authState.triedSilentRefresh = true
      return false
    })
    await router.push('/queue')
    expect(authState.refresh).toHaveBeenCalledTimes(1)
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/queue')
  })

  it('does not retry refresh a second time once triedSilentRefresh is already true', async () => {
    authState.setupComplete = true
    authState.isLoggedIn = false
    authState.triedSilentRefresh = true
    await router.push('/queue')
    expect(authState.refresh).not.toHaveBeenCalled()
    expect(router.currentRoute.value.name).toBe('login')
  })

  it('lets the navigation through when the refresh succeeds and logs the user in', async () => {
    authState.setupComplete = true
    authState.isLoggedIn = false
    authState.triedSilentRefresh = false
    authState.refresh.mockImplementation(async () => {
      authState.isLoggedIn = true
      return true
    })
    await router.push('/queue')
    expect(router.currentRoute.value.name).toBe('queue')
  })

  it('lets an already-logged-in user straight through to a protected route', async () => {
    authState.setupComplete = true
    authState.isLoggedIn = true
    await router.push('/settings')
    expect(router.currentRoute.value.name).toBe('settings')
    expect(authState.refresh).not.toHaveBeenCalled()
  })

  it('resolves every declared route to its own lazy-loaded component when auth allows it through', async () => {
    authState.setupComplete = true
    authState.isLoggedIn = true
    for (const route of ROUTES) {
      const target = route.path.replace(/:[^/]+/g, '42')
      await router.push(target)
      expect(router.currentRoute.value.name, `navigating to ${target}`).toBe(route.name)
    }
  })
})
