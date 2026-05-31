// frontend/vue/src/stores/auth.js
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api from '@/api/client'

const ACCESS_TOKEN_KEY  = 'hygie_token'
const REFRESH_TOKEN_KEY = 'hygie_refresh_token'
const ACCESS_TTL_MS     = 60 * 60 * 1000   // 1h — matches backend ACCESS_TOKEN_EXPIRE_MINUTES
const REFRESH_BEFORE_MS = 5  * 60 * 1000   // refresh 5min before expiry

export const useAuthStore = defineStore('auth', () => {
  const token         = ref(localStorage.getItem(ACCESS_TOKEN_KEY)  || '')
  const refreshToken  = ref(localStorage.getItem(REFRESH_TOKEN_KEY) || '')
  const username      = ref('')
  const setupComplete = ref(null)

  let _refreshTimer = null

  const isLoggedIn = computed(() => !!token.value)

  // ── Token storage ──────────────────────────────────────────────────────────
  function _setTokens(access, refresh) {
    token.value        = access
    if (refresh) refreshToken.value = refresh
    localStorage.setItem(ACCESS_TOKEN_KEY, access)
    if (refresh) localStorage.setItem(REFRESH_TOKEN_KEY, refresh)
    _scheduleRefresh()
  }

  function _clearTokens() {
    token.value        = ''
    refreshToken.value = ''
    localStorage.removeItem(ACCESS_TOKEN_KEY)
    localStorage.removeItem(REFRESH_TOKEN_KEY)
    if (_refreshTimer) clearTimeout(_refreshTimer)
    _refreshTimer = null
  }

  // ── Auto-refresh scheduling ────────────────────────────────────────────────
  function _scheduleRefresh() {
    if (_refreshTimer) clearTimeout(_refreshTimer)
    if (!refreshToken.value) return
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
    _setTokens(data.access_token || data.token, data.refresh_token)
  }

  async function login(u, p) {
    const { data } = await api.post('/auth/login', { username: u, password: p })
    username.value = data.username || u
    _setTokens(data.access_token || data.token, data.refresh_token)
  }

  async function refresh() {
    if (!refreshToken.value) return false
    try {
      const { data } = await api.post('/auth/refresh', {
        refresh_token: refreshToken.value,
      })
      const newAccess = data.access_token || data.token
      if (newAccess) {
        _setTokens(newAccess, null)   // keep same refresh token
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
    if (refreshToken.value) {
      try {
        await api.post('/auth/logout', { refresh_token: refreshToken.value })
      } catch { /* best-effort server-side revocation */ }
    }
    _clearTokens()
  }

  // Schedule refresh on store init if already logged in
  if (token.value && refreshToken.value) {
    _scheduleRefresh()
  }

  return {
    token, refreshToken, username, setupComplete, isLoggedIn,
    checkSetup, setup, login, refresh, fetchMe, logout,
  }
})
