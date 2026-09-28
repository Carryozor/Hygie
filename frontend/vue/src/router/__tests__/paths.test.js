// src/router/__tests__/paths.test.js
//
// router/paths.js is the single source of truth for which top-level paths
// require auth. router/index.js builds its route table from it, and
// api/client.js's 401-refresh interceptor uses it to decide whether the
// current page is public (skip refresh) or protected (attempt refresh).
// Before this file, client.js hardcoded its own KNOWN_PROTECTED array —
// a new protected single-segment route (e.g. `/reports`) added only to the
// router table would silently be treated as the public `/:slug` page and
// skip the 401->refresh flow entirely.
import { describe, it, expect } from 'vitest'
import { ROUTES, isProtectedPath } from '../paths'

describe('ROUTES', () => {
  it('lists /login and /setup as public', () => {
    const login = ROUTES.find(r => r.path === '/login')
    const setup = ROUTES.find(r => r.path === '/setup')
    expect(login.public).toBe(true)
    expect(setup.public).toBe(true)
  })

  it('lists the dashboard, queue, rules, settings, logs, ignored, calendar, library routes as protected', () => {
    const protectedPaths = ['/', '/queue', '/rules', '/settings', '/logs', '/ignored', '/calendar', '/library/:id']
    for (const p of protectedPaths) {
      const route = ROUTES.find(r => r.path === p)
      expect(route, `expected a route for ${p}`).toBeTruthy()
      expect(route.public).toBe(false)
    }
  })

  it('keeps the /:slug public catch-all last', () => {
    expect(ROUTES.at(-1).path).toBe('/:slug')
    expect(ROUTES.at(-1).public).toBe(true)
  })
})

describe('isProtectedPath', () => {
  it('treats /login and /setup as not protected', () => {
    expect(isProtectedPath('/login')).toBe(false)
    expect(isProtectedPath('/setup')).toBe(false)
  })

  it('treats the dashboard "/" as protected', () => {
    expect(isProtectedPath('/')).toBe(true)
  })

  it('treats static protected routes as protected', () => {
    for (const p of ['/queue', '/rules', '/settings', '/logs', '/ignored', '/calendar']) {
      expect(isProtectedPath(p)).toBe(true)
    }
  })

  it('treats a parametrized /library/:id path as protected', () => {
    expect(isProtectedPath('/library/42')).toBe(true)
    expect(isProtectedPath('/library/abc-def')).toBe(true)
  })

  it('treats an unknown single-segment path as the public calendar slug', () => {
    expect(isProtectedPath('/some-calendar-slug')).toBe(false)
  })

  it('treats a totally unknown multi-segment path as protected (fail closed)', () => {
    expect(isProtectedPath('/unknown/nested/path')).toBe(true)
  })
})
