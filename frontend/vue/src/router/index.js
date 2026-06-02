import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes = [
  { path: '/setup',       name: 'setup',     component: () => import('@/views/SetupView.vue'),    meta: { public: true } },
  { path: '/login',       name: 'login',     component: () => import('@/views/LoginView.vue'),    meta: { public: true } },
  { path: '/',            name: 'dashboard', component: () => import('@/views/DashboardView.vue') },
  { path: '/library/:id', name: 'library',   component: () => import('@/views/LibraryView.vue')  },
  { path: '/queue',       name: 'queue',     component: () => import('@/views/QueueView.vue')     },
  { path: '/calendar',    name: 'calendar',  component: () => import('@/views/CalendarView.vue')  },
  { path: '/rules',       name: 'rules',     component: () => import('@/views/RulesView.vue')     },
  { path: '/settings',    name: 'settings',  component: () => import('@/views/SettingsView.vue')  },
  { path: '/logs',        name: 'logs',      component: () => import('@/views/LogsView.vue')      },
  { path: '/ignored',     name: 'ignored',   component: () => import('@/views/IgnoredView.vue')   },
  // Public calendar — catch-all for unknown paths. Must stay last.
  // URL format: /myslug  (no /public/ prefix)
  { path: '/:slug',    name: 'public',   component: () => import('@/views/PublicView.vue'),    meta: { public: true } },
]

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
  if (!auth.isLoggedIn) return { name: 'login', query: { redirect: to.fullPath } }
  return true
})

export default router
