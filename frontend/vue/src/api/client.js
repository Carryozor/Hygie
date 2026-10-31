// frontend/vue/src/api/client.js
import axios from 'axios'
import { installErrorInterceptor } from './errorHandler'
import { getToken } from './tokenStore'
import { refreshOnce } from './refreshCoordinator'
import { isProtectedPath } from '@/router/paths'

const api = axios.create({ baseURL: '/api' })

// ── Request — inject access token ─────────────────────────────────────────────
api.interceptors.request.use(cfg => {
  const token = getToken()
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

// ── Response — handle 401 with silent token refresh ───────────────────────────
// refreshOnce() is single-flight and shared with the router guard / store
// timer (see refreshCoordinator.js): it updates tokenStore, announces the new
// token to the Pinia store via an event, and on failure clears everything and
// dispatches hygie:unauthorized.
api.interceptors.response.use(
  r => r,
  async err => {
    const originalReq  = err.config
    // Public routes (/login, /setup, and the /{slug} public calendar page)
    // must never trigger the refresh dance. isProtectedPath() is the same
    // source of truth router/index.js builds its route table from — see
    // router/paths.js — so a new protected route can't silently fall
    // through to being treated as the public /:slug page.
    const pathname     = window.location.pathname
    const isPublicPage = !isProtectedPath(pathname)
    const is401        = err.response?.status === 401
    // Skip retry loop for auth endpoints themselves
    const isAuthEndpoint = originalReq?.url?.includes('/auth/')

    if (is401 && !isPublicPage && !isAuthEndpoint && !originalReq._retried) {
      originalReq._retried = true
      const newToken = await refreshOnce()   // rejects (after cleanup) on failure
      originalReq.headers.Authorization = `Bearer ${newToken}`
      return api(originalReq)
    }

    return Promise.reject(err)
  }
)

installErrorInterceptor(api)

export default api
