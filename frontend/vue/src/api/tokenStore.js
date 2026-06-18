// frontend/vue/src/api/tokenStore.js
/**
 * In-memory holder for the short-lived JWT access token.
 *
 * Deliberately NOT persisted to localStorage/sessionStorage — those are
 * readable by any injected script, and Hygie's own security rules say to
 * never persist raw auth tokens there. Losing the token on page reload is
 * expected: the 30-day refresh token lives in an httpOnly cookie the backend
 * set, invisible to JS, and the app re-mints a fresh access token from it via
 * POST /auth/refresh on boot (see router/index.js's navigation guard).
 *
 * A plain module-level variable (rather than importing the Pinia auth store
 * here) avoids a circular import: stores/auth.js already imports the axios
 * client from this module's sibling file.
 */
let _token = ''

export function getToken() {
  return _token
}

export function setToken(value) {
  _token = value || ''
}

export function clearToken() {
  _token = ''
}
