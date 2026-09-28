// src/router/__tests__/route-table-drift.test.js
//
// Regression test for the drift bug this refactor fixes: router/index.js's
// route table and api/client.js's 401-refresh "is this page public?" check
// used to be two independently hand-maintained lists. Both now derive from
// router/paths.js, so this test walks the REAL router table (not paths.js's
// own ROUTES export, to actually exercise router/index.js's construction)
// and asserts every route resolves through isProtectedPath() — the same
// function api/client.js calls — with the sign matching its own
// `meta.public` flag.
import { describe, it, expect } from 'vitest'
import router from '@/router'
import { isProtectedPath } from '../paths'

function exampleUrl(path) {
  // Fill any :param segments with a sample value so we get a concrete
  // pathname to test, e.g. '/library/:id' -> '/library/42'.
  return path.replace(/:[^/]+/g, '42')
}

describe('router table vs. api/client.js protected-path detection', () => {
  const records = router.getRoutes()

  it('the router actually has routes (sanity check the import worked)', () => {
    expect(records.length).toBeGreaterThan(0)
  })

  it('every protected route in the router table is recognised as protected', () => {
    const protectedRecords = records.filter(r => !r.meta?.public)
    expect(protectedRecords.length).toBeGreaterThan(0) // don't let this silently degrade to 0 routes
    for (const r of protectedRecords) {
      expect(isProtectedPath(exampleUrl(r.path)), `expected ${r.path} to be protected`).toBe(true)
    }
  })

  it('every public route in the router table is recognised as NOT protected', () => {
    const publicRecords = records.filter(r => r.meta?.public)
    expect(publicRecords.length).toBeGreaterThan(0)
    for (const r of publicRecords) {
      // Skip the /:slug catch-all itself — it's a pattern, not a real URL;
      // isProtectedPath is exercised against real slugs elsewhere.
      if (r.path === '/:slug') continue
      expect(isProtectedPath(exampleUrl(r.path)), `expected ${r.path} to be public`).toBe(false)
    }
  })

  it('router/index.js registers exactly the routes from router/paths.js (name-for-name)', async () => {
    const { ROUTES } = await import('../paths')
    const routerNames = new Set(records.map(r => r.name))
    for (const r of ROUTES) {
      expect(routerNames.has(r.name), `router table is missing route "${r.name}"`).toBe(true)
    }
    expect(routerNames.size).toBe(ROUTES.length)
  })
})
