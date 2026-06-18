// frontend/vue/src/api/client.js
import axios from 'axios'
import { installErrorInterceptor } from './errorHandler'
import { getToken, setToken, clearToken } from './tokenStore'

const api = axios.create({ baseURL: '/api' })

// ── Request — inject access token ─────────────────────────────────────────────
api.interceptors.request.use(cfg => {
  const token = getToken()
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

// ── Response — handle 401 with silent token refresh ───────────────────────────
let _isRefreshing = false
let _refreshQueue = []   // [{resolve, reject}] — queued requests during refresh

function _processQueue(error, token = null) {
  _refreshQueue.forEach(({ resolve, reject }) => {
    if (error) reject(error)
    else resolve(token)
  })
  _refreshQueue = []
}

api.interceptors.response.use(
  r => r,
  async err => {
    const originalReq  = err.config
    // Public routes: /login, /setup, and any /{slug} path (public calendar).
    // The public calendar URL is /{slug} — a single-segment path that is NOT
    // one of the known app routes. We detect it by checking the Vue router meta,
    // or conservatively: any path that doesn't start with a known protected prefix.
    const pathname = window.location.pathname
    const KNOWN_PROTECTED = ['/', '/queue', '/calendar', '/rules', '/settings', '/logs', '/ignored', '/library']
    const isPublicPage = ['/login', '/setup'].includes(pathname)
      || (!KNOWN_PROTECTED.some(p => pathname === p || pathname.startsWith('/library/'))
          && pathname.split('/').length === 2)  // single-segment path = public slug
    const is401        = err.response?.status === 401
    // Skip retry loop for auth endpoints themselves
    const isAuthEndpoint = originalReq?.url?.includes('/auth/')

    if (is401 && !isPublicPage && !isAuthEndpoint && !originalReq._retried) {
      if (_isRefreshing) {
        // Another refresh in progress — queue this request
        return new Promise((resolve, reject) => {
          _refreshQueue.push({ resolve, reject })
        }).then(newToken => {
          originalReq.headers.Authorization = `Bearer ${newToken}`
          originalReq._retried = true
          return api(originalReq)
        })
      }

      _isRefreshing        = true
      originalReq._retried = true

      try {
        // Use plain axios (not api) to avoid interceptor loop.
        // The refresh token travels in an httpOnly cookie (same-origin —
        // sent automatically); the body carries only a legacy localStorage
        // token from pre-cookie sessions, purged after first use.
        const { data } = await axios.post('/api/auth/refresh', {
          refresh_token: localStorage.getItem('hygie_refresh_token') || '',
        })
        const newToken = data.access_token || data.token
        setToken(newToken)
        localStorage.removeItem('hygie_refresh_token')
        api.defaults.headers.common.Authorization = `Bearer ${newToken}`
        _processQueue(null, newToken)
        originalReq.headers.Authorization = `Bearer ${newToken}`
        return api(originalReq)
      } catch (refreshErr) {
        _processQueue(refreshErr)
        clearToken()
        localStorage.removeItem('hygie_refresh_token')
        window.dispatchEvent(new Event('hygie:unauthorized'))
        return Promise.reject(refreshErr)
      } finally {
        _isRefreshing = false
      }
    }

    return Promise.reject(err)
  }
)

installErrorInterceptor(api)

export default api
