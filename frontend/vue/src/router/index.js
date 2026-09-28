import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { ROUTES } from './paths'

// Route path/public-vs-protected data lives in ./paths.js (also consumed by
// api/client.js) so the two can never drift. Only the lazy-loaded view
// components are wired up here.
const COMPONENTS = {
  setup:     () => import('@/views/SetupView.vue'),
  login:     () => import('@/views/LoginView.vue'),
  dashboard: () => import('@/views/DashboardView.vue'),
  library:   () => import('@/views/LibraryView.vue'),
  queue:     () => import('@/views/QueueView.vue'),
  calendar:  () => import('@/views/CalendarView.vue'),
  rules:     () => import('@/views/RulesView.vue'),
  settings:  () => import('@/views/SettingsView.vue'),
  logs:      () => import('@/views/LogsView.vue'),
  ignored:   () => import('@/views/IgnoredView.vue'),
  // Public calendar — catch-all for unknown paths (/:slug). Must stay last;
  // enforced by ROUTES' own ordering in paths.js. URL format: /myslug (no
  // /public/ prefix).
  public:    () => import('@/views/PublicView.vue'),
}

const routes = ROUTES.map(({ name, path, public: isPublic }) => ({
  path,
  name,
  component: COMPONENTS[name],
  meta: { public: isPublic },
}))

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async to => {
  if (to.meta.public) return true
  const auth = useAuthStore()
  // Only call the API once — setupComplete is null until first check
  const setup = auth.setupComplete !== null ? auth.setupComplete : await auth.checkSetup()
  if (!setup) return { name: 'setup' }
  // The access token lives in memory only (see api/tokenStore.js) and does not
  // survive a page reload — try once to re-mint it from the httpOnly refresh
  // cookie before treating the user as logged out.
  if (!auth.isLoggedIn && !auth.triedSilentRefresh) {
    await auth.refresh()
  }
  if (!auth.isLoggedIn) return { name: 'login', query: { redirect: to.fullPath } }
  return true
})

export default router
