// frontend/vue/src/stores/auth.js
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api from '@/api/client'
import { getToken, setToken, clearToken } from '@/api/tokenStore'

// Pre-cookie versions persisted the refresh token here — now it lives in an
// httpOnly cookie. The key is only read once for migration, then purged.
const LEGACY_REFRESH_KEY = 'hygie_refresh_token'
const ACCESS_TTL_MS     = 60 * 60 * 1000   // 1h — matches backend ACCESS_TOKEN_EXPIRE_MINUTES
const REFRESH_BEFORE_MS = 5  * 60 * 1000   // refresh 5min before expiry

export const useAuthStore = defineStore('auth', () => {
  // In-memory only (see api/tokenStore.js) — never persisted to
  // localStorage/sessionStorage, so it does not survive a page reload.
  // triedSilentRefresh tracks whether router/index.js already attempted to
  // re-mint it from the httpOnly refresh cookie this session.
  const token              = ref(getToken())
  const username            = ref('')
  const setupComplete       = ref(null)
  const triedSilentRefresh  = ref(false)

  let _refreshTimer = null

  const isLoggedIn = computed(() => !!token.value)

  // ── Token storage ──────────────────────────────────────────────────────────
  // The 30-day refresh token is an httpOnly cookie set by the backend —
  // invisible to JS. The access token lives only in memory (tokenStore.js).
  function _setAccessToken(access) {
    token.value = access
    setToken(access)
    _scheduleRefresh()
  }

  function _clearTokens() {
    token.value = ''
    clearToken()
    localStorage.removeItem(LEGACY_REFRESH_KEY)
    if (_refreshTimer) clearTimeout(_refreshTimer)
    _refreshTimer = null
  }

  // ── Auto-refresh scheduling ────────────────────────────────────────────────
  function _scheduleRefresh() {
    if (_refreshTimer) clearTimeout(_refreshTimer)
    if (!token.value) return
    const delay = ACCESS_TTL_MS - REFRESH_BEFORE_MS  // 55 minutes
    _refreshTimer = setTimeout(refresh, delay > 0 ? delay : 1000)
  }

  // ── Actions ────────────────────────────────────────────────────────────────
  async function checkSetup() {
    const { data } = await api.get('/auth/status')
    setupComplete.value = data.setup_complete
    return data.setup_complete
  }

  async function setup(u, p) {
    const { data } = await api.post('/auth/setup', { username: u, password: p })
    username.value = data.username || u
    _setAccessToken(data.access_token || data.token)
  }

  async function login(u, p) {
    const { data } = await api.post('/auth/login', { username: u, password: p })
    username.value = data.username || u
    _setAccessToken(data.access_token || data.token)
  }

  async function refresh() {
    triedSilentRefresh.value = true
    // The httpOnly cookie carries the refresh token; the body field is only
    // used to migrate sessions created before the cookie change.
    const legacy = localStorage.getItem(LEGACY_REFRESH_KEY) || ''
    try {
      const { data } = await api.post('/auth/refresh', { refresh_token: legacy })
      const newAccess = data.access_token || data.token
      if (newAccess) {
        localStorage.removeItem(LEGACY_REFRESH_KEY)
        _setAccessToken(newAccess)
        return true
      }
    } catch {
      _clearTokens()
      window.dispatchEvent(new Event('hygie:unauthorized'))
    }
    return false
  }

  async function fetchMe() {
    if (!token.value) return
    try {
      const { data } = await api.get('/auth/me')
      username.value = data.username
    } catch { /* silent */ }
  }

  async function logout() {
    try {
      // Revokes the cookie-held refresh token server-side and clears the cookie
      await api.post('/auth/logout', {
        refresh_token: localStorage.getItem(LEGACY_REFRESH_KEY) || '',
      })
    } catch { /* best-effort server-side revocation */ }
    _clearTokens()
  }

  // Schedule refresh on store init if already logged in
  if (token.value) {
    _scheduleRefresh()
  }

  return {
    token, username, setupComplete, isLoggedIn, triedSilentRefresh,
    checkSetup, setup, login, refresh, fetchMe, logout,
  }
})
