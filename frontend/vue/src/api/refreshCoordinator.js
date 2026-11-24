// frontend/vue/src/api/refreshCoordinator.js
/**
 * Single-flight coordinator for POST /auth/refresh.
 *
 * The router guard / store timer (stores/auth.js) and the 401 interceptor
 * (api/client.js) can both need a fresh access token at the same moment (page
 * reload). Each would POST /auth/refresh, rotating the refresh cookie twice
 * and leaving an orphaned 30-day token behind. Both now call refreshOnce(),
 * which shares one in-flight promise.
 *
 * Lives in its own module with only axios + tokenStore as dependencies so
 * neither the store nor the client has to import the other (see the circular
 * import note in tokenStore.js). The store is kept in sync through window
 * events rather than an import:
 *   hygie:token-refreshed  { detail: { token } }  — success
 *   hygie:unauthorized                             — the server REJECTED the
 *                                                    refresh token (401/403)
 *
 * Only a 401/403 ends the session. A 502 from the proxy, a 429 or a network
 * blip is transient: the token is kept and the caller may retry. (Treating
 * them as "unauthorized" made App.vue call /auth/logout, which now revokes the
 * still-valid 30-day cookie — one blip would log the user out for good.)
 */
import axios from 'axios'
import { setToken, clearToken } from './tokenStore'

const LEGACY_REFRESH_KEY = 'hygie_refresh_token'
export const TOKEN_REFRESHED_EVENT = 'hygie:token-refreshed'
export const UNAUTHORIZED_EVENT = 'hygie:unauthorized'

let _inflight = null
// Bumped by invalidateRefresh() (logout): a refresh that completes after that
// must not store a token or announce anything, or it would resurrect the session.
let _generation = 0

/** True when the server rejected the refresh token (session really over). */
export function isAuthFailure(err) {
  const status = err?.response?.status
  return status === 401 || status === 403
}

/** Drop any in-flight refresh so its late result is ignored (call on logout). */
export function invalidateRefresh() {
  _generation += 1
  _inflight = null
}

async function _doRefresh() {
  const generation = _generation
  try {
    // Plain axios (not the api instance) so the 401 interceptor cannot loop.
    // The refresh token travels in the httpOnly cookie; the body only carries
    // a legacy localStorage token from pre-cookie sessions, purged after use.
    const { data } = await axios.post('/api/auth/refresh', {
      refresh_token: localStorage.getItem(LEGACY_REFRESH_KEY) || '',
    })
    const token = data?.access_token || data?.token
    if (!token) throw new Error('empty refresh response')
    if (generation !== _generation) throw new Error('refresh superseded by logout')
    setToken(token)
    localStorage.removeItem(LEGACY_REFRESH_KEY)
    window.dispatchEvent(new CustomEvent(TOKEN_REFRESHED_EVENT, { detail: { token } }))
    return token
  } catch (err) {
    if (isAuthFailure(err) && generation === _generation) {
      clearToken()
      localStorage.removeItem(LEGACY_REFRESH_KEY)
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    }
    throw err
  }
}

/** Resolve with a fresh access token; concurrent callers share one request. */
export function refreshOnce() {
  if (!_inflight) {
    // Only clear the slot if it still holds THIS refresh: after
    // invalidateRefresh() a newer refresh may own it, and a superseded one
    // settling late must not erase it (that caused a double cookie rotation).
    const p = _doRefresh().finally(() => { if (_inflight === p) _inflight = null })
    _inflight = p
  }
  return _inflight
}
