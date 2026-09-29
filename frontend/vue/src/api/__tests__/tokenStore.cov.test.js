// src/api/__tests__/tokenStore.cov.test.js
// Gap-fill for api/tokenStore.js: setToken(undefined/null) must clear the
// in-memory token to '' rather than storing a falsy sentinel — auth.js's
// isLoggedIn computed relies on `!!token.value`, so this must not become
// a "truthy empty string trap" that later reads as logged-in.
import { describe, it, expect, afterEach } from 'vitest'
import { getToken, setToken, clearToken } from '../tokenStore'

describe('tokenStore — falsy value handling', () => {
  afterEach(() => clearToken())

  it('setToken(undefined) resets the token to an empty string', () => {
    setToken('real-token')
    setToken(undefined)
    expect(getToken()).toBe('')
  })

  it('setToken(null) resets the token to an empty string', () => {
    setToken('real-token')
    setToken(null)
    expect(getToken()).toBe('')
  })

  it('setToken with a real value round-trips exactly through getToken', () => {
    setToken('abc.def.ghi')
    expect(getToken()).toBe('abc.def.ghi')
  })
})
