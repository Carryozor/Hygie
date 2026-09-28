// frontend/vue/src/router/paths.js
/**
 * Single source of truth for the app's top-level paths and whether each one
 * is public or requires auth.
 *
 * router/index.js builds its `routes` table from this list (pairing each
 * entry with its lazy-loaded view component), and api/client.js's 401
 * response interceptor calls isProtectedPath() to decide whether the
 * current page should attempt a silent token refresh.
 *
 * Kept dependency-free (no vue-router, no stores) on purpose: client.js
 * cannot import router/index.js (stores/auth.js -> api/client.js, and
 * router/index.js -> stores/auth.js, would cycle back), but it can safely
 * import this file.
 *
 * The catch-all `/:slug` public calendar page MUST stay last — it matches
 * any single-segment path not claimed by an earlier, more specific entry.
 */
export const ROUTES = [
  { name: 'setup',     path: '/setup',       public: true },
  { name: 'login',     path: '/login',       public: true },
  { name: 'dashboard', path: '/',            public: false },
  { name: 'library',   path: '/library/:id', public: false },
  { name: 'queue',     path: '/queue',       public: false },
  { name: 'calendar',  path: '/calendar',    public: false },
  { name: 'rules',     path: '/rules',       public: false },
  { name: 'settings',  path: '/settings',    public: false },
  { name: 'logs',      path: '/logs',        public: false },
  { name: 'ignored',   path: '/ignored',     public: false },
  { name: 'public',    path: '/:slug',       public: true },
]

function pathToRegex(path) {
  const pattern = path
    .split('/')
    .map(segment => (segment.startsWith(':') ? '[^/]+' : segment.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
    .join('/')
  return new RegExp(`^${pattern}$`)
}

const MATCHERS = ROUTES.map(route => ({ ...route, regex: pathToRegex(route.path) }))

/**
 * True when `pathname` requires auth. Unknown paths that don't match any
 * known route (including the /:slug catch-all — can only happen for a
 * multi-segment path) fail CLOSED (treated as protected), so an
 * unrecognized URL never silently skips the refresh flow.
 */
export function isProtectedPath(pathname) {
  const match = MATCHERS.find(m => m.regex.test(pathname))
  if (!match) return true
  return !match.public
}
