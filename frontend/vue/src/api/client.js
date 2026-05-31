// frontend/vue/src/api/client.js
import axios from 'axios'
import { installErrorInterceptor } from './errorHandler'

const api = axios.create({ baseURL: '/api' })

// ── Request — inject access token ─────────────────────────────────────────────
api.interceptors.request.use(cfg => {
  const token = localStorage.getItem('hygie_token')
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
    const isPublicPage = ['/login', '/setup', '/public'].includes(window.location.pathname)
    const is401        = err.response?.status === 401
    // Skip retry loop for auth endpoints themselves
    const isAuthEndpoint = originalReq?.url?.includes('/auth/')

    if (is401 && !isPublicPage && !isAuthEndpoint && !originalReq._retried) {
      const refreshToken = localStorage.getItem('hygie_refresh_token')

      if (!refreshToken) {
        localStorage.removeItem('hygie_token')
        window.dispatchEvent(new Event('hygie:unauthorized'))
        return Promise.reject(err)
      }

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
        // Use plain axios (not api) to avoid interceptor loop
        const { data } = await axios.post('/api/auth/refresh', {
          refresh_token: refreshToken,
        })
        const newToken = data.access_token || data.token
        localStorage.setItem('hygie_token', newToken)
        api.defaults.headers.common.Authorization = `Bearer ${newToken}`
        _processQueue(null, newToken)
        originalReq.headers.Authorization = `Bearer ${newToken}`
        return api(originalReq)
      } catch (refreshErr) {
        _processQueue(refreshErr)
        localStorage.removeItem('hygie_token')
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
