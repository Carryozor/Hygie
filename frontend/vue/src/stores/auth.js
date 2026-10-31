// frontend/vue/src/stores/auth.js
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api from '@/api/client'
import { getToken, setToken, clearToken } from '@/api/tokenStore'
import {
  refreshOnce, invalidateRefresh, isAuthFailure,
  TOKEN_REFRESHED_EVENT, UNAUTHORIZED_EVENT,
} from '@/api/refreshCoordinator'

// Pre-cookie versions persisted the refresh token here — now it lives in an
// httpOnly cookie. The key is only read once for migration, then purged.
const LEGACY_REFRESH_KEY = 'hygie_refresh_token'
const ACCESS_TTL_MS     = 60 * 60 * 1000   // 1h — matches backend ACCESS_TOKEN_EXPIRE_MINUTES
const REFRESH_BEFORE_MS = 5  * 60 * 1000   // refresh 5min before expiry
const RETRY_AFTER_TRANSIENT_MS = 60 * 1000 // retry delay after a non-auth refresh failure

let _detachSync = null

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
    try {
      // Single-flight: shared with the 401 interceptor (api/client.js). The
      // coordinator already dispatched hygie:unauthorized on a real rejection.
      _setAccessToken(await refreshOnce())
      return true
    } catch (err) {
      if (isAuthFailure(err) || !token.value) {
        _clearTokens()
      } else {
        // Transient (502/429/network): keep the session, try again shortly.
        if (_refreshTimer) clearTimeout(_refreshTimer)
        _refreshTimer = setTimeout(refresh, RETRY_AFTER_TRANSIENT_MS)
      }
      return false
    }
  }

  async function fetchMe() {
    if (!token.value) return
    try {
      const { data } = await api.get('/auth/me')
      username.value = data.username
    } catch { /* silent */ }
  }

  async function logout() {
    // A refresh still in flight must not resurrect the session once we are out.
    invalidateRefresh()
    try {
      // Authenticated by the refresh cookie alone (works with an expired
      // access token): revokes it server-side and clears the cookie.
      await api.post('/auth/logout', {
        refresh_token: localStorage.getItem(LEGACY_REFRESH_KEY) || '',
      })
    } catch { /* best-effort server-side revocation */ }
    // Local state is cleaned whatever happened above.
    if (api.defaults?.headers?.common) delete api.defaults.headers.common.Authorization
    _clearTokens()
  }

  // Keep in sync with refreshes/failures initiated outside the store (the 401
  // interceptor goes through api/refreshCoordinator.js, not through this store).
  // Only the most recently created store listens (one store per app; this
  // avoids stale listeners piling up if the store is re-created, e.g. in tests).
  if (typeof window !== 'undefined') {
    _detachSync?.()
    const onRefreshed = e => {
      if (e.detail?.token && e.detail.token !== token.value) _setAccessToken(e.detail.token)
    }
    const onUnauthorized = () => { if (token.value) _clearTokens() }
    window.addEventListener(TOKEN_REFRESHED_EVENT, onRefreshed)
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    _detachSync = () => {
      window.removeEventListener(TOKEN_REFRESHED_EVENT, onRefreshed)
      window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    }
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
