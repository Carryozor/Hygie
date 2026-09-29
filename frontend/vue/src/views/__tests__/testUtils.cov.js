// src/views/__tests__/testUtils.cov.js
//
// Shared mounting helpers for the cov/front-views test files. NOT a test
// file itself (no .test.js suffix -> vitest's `include: ['src/**/*.test.js']`
// never picks it up as a suite). Centralizes pinia/i18n/router wiring so each
// view test only has to describe what differs (route, api mocks).
import { createI18n } from 'vue-i18n'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import fr from '@/locales/fr.json'

const Blank = { template: '<div />' }

// Superset of router/paths.js ROUTES, with dummy components for whichever
// view isn't the one under test — a view under test may still contain a
// <router-link> or call useRouter().push('/somewhere').
// meta.public mirrors router/paths.js ROUTES exactly (App.vue and the real
// router guard both branch on route.meta.public).
export const TEST_ROUTES = [
  { name: 'setup',     path: '/setup',       component: Blank, meta: { public: true } },
  { name: 'login',     path: '/login',       component: Blank, meta: { public: true } },
  { name: 'dashboard', path: '/',            component: Blank, meta: { public: false } },
  { name: 'library',   path: '/library/:id', component: Blank, meta: { public: false } },
  { name: 'queue',     path: '/queue',       component: Blank, meta: { public: false } },
  { name: 'calendar',  path: '/calendar',    component: Blank, meta: { public: false } },
  { name: 'rules',     path: '/rules',       component: Blank, meta: { public: false } },
  { name: 'settings',  path: '/settings',    component: Blank, meta: { public: false } },
  { name: 'logs',      path: '/logs',        component: Blank, meta: { public: false } },
  { name: 'ignored',   path: '/ignored',     component: Blank, meta: { public: false } },
  { name: 'public',    path: '/:slug',       component: Blank, meta: { public: true } },
]

export function makeI18n() {
  return createI18n({ legacy: false, locale: 'fr', fallbackLocale: 'fr', messages: { fr } })
}

/**
 * Build a fresh memory router pre-navigated to `path`, and a fresh active
 * pinia. Call before mounting a view so useRoute()/useRouter()/store
 * composables resolve correctly.
 */
export async function setupTestEnv(path = '/') {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({ history: createMemoryHistory(), routes: TEST_ROUTES })
  router.push(path)
  await router.isReady()
  return { pinia, router, i18n: makeI18n() }
}

/**
 * Mount `Component` with pinia + i18n + memory router wired up.
 * `path` navigates the router before mount (default '/').
 * `stubs` lets a view's heavy child components (modals, settings tabs) be
 * shallow-stubbed instead of fully rendered.
 */
export async function mountView(Component, { path = '/', props = {}, stubs = {} } = {}) {
  const { pinia, router, i18n } = await setupTestEnv(path)
  const wrapper = mount(Component, {
    props,
    global: { plugins: [pinia, router, i18n], stubs },
  })
  await flushAll()
  return { wrapper, router, pinia }
}

// Drains pending microtasks + one tick — enough for a chain of awaited
// api.get()/api.post() mocks (vi.fn().mockResolvedValue) to settle and for
// Vue to re-render.
export async function flushAll() {
  await Promise.resolve()
  await Promise.resolve()
  await new Promise(r => setTimeout(r, 0))
}
