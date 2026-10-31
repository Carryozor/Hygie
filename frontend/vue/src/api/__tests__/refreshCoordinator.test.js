// Single-flight refresh: the router guard, the store timer and the 401
// interceptor must share ONE in-flight POST /auth/refresh.
import { describe, it, expect, vi, beforeEach } from 'vitest'

const axiosPost = vi.hoisted(() => vi.fn())
vi.mock('axios', () => ({ default: { post: axiosPost } }))

import { refreshOnce, invalidateRefresh, isAuthFailure } from '../refreshCoordinator'
import { getToken, setToken, clearToken } from '../tokenStore'

describe('refreshOnce', () => {
  beforeEach(() => {
    axiosPost.mockReset()
    clearToken()
    localStorage.clear()
  })

  it('two simultaneous callers produce a single POST /api/auth/refresh', async () => {
    let resolve
    axiosPost.mockReturnValue(new Promise(r => { resolve = r }))
    const a = refreshOnce()
    const b = refreshOnce()
    resolve({ data: { access_token: 'tok-1' } })
    expect(await a).toBe('tok-1')
    expect(await b).toBe('tok-1')
    expect(axiosPost).toHaveBeenCalledTimes(1)
    expect(axiosPost).toHaveBeenCalledWith('/api/auth/refresh', { refresh_token: '' })
    expect(getToken()).toBe('tok-1')
  })

  it('a new refresh can start once the previous one settled', async () => {
    axiosPost.mockResolvedValue({ data: { token: 'tok-legacy-field' } })
    await refreshOnce()
    await refreshOnce()
    expect(axiosPost).toHaveBeenCalledTimes(2)
  })

  it('announces the new token with hygie:token-refreshed', async () => {
    axiosPost.mockResolvedValue({ data: { access_token: 'tok-2' } })
    const seen = []
    const l = e => seen.push(e.detail.token)
    window.addEventListener('hygie:token-refreshed', l)
    await refreshOnce()
    window.removeEventListener('hygie:token-refreshed', l)
    expect(seen).toEqual(['tok-2'])
  })

  it('failure clears tokenStore, dispatches hygie:unauthorized once, and rejects every waiter', async () => {
    setToken('stale')
    axiosPost.mockRejectedValue({ response: { status: 401 } })
    const events = []
    const l = () => events.push(1)
    window.addEventListener('hygie:unauthorized', l)
    const results = await Promise.allSettled([refreshOnce(), refreshOnce()])
    window.removeEventListener('hygie:unauthorized', l)
    expect(results.map(r => r.status)).toEqual(['rejected', 'rejected'])
    expect(events.length).toBe(1)
    expect(getToken()).toBe('')
    expect(axiosPost).toHaveBeenCalledTimes(1)
  })

  it('an empty token in the response counts as a failure', async () => {
    axiosPost.mockResolvedValue({ data: {} })
    await expect(refreshOnce()).rejects.toBeTruthy()
    expect(getToken()).toBe('')
  })

  it('sends and purges the legacy localStorage refresh token', async () => {
    localStorage.setItem('hygie_refresh_token', 'legacy')
    axiosPost.mockResolvedValue({ data: { access_token: 'x' } })
    await refreshOnce()
    expect(axiosPost).toHaveBeenCalledWith('/api/auth/refresh', { refresh_token: 'legacy' })
    expect(localStorage.getItem('hygie_refresh_token')).toBeNull()
  })

  // ── Review fixes: a transient failure must NOT end the session ────────────
  it.each([
    ['502 from the proxy', { response: { status: 502 } }],
    ['429 rate limit',      { response: { status: 429 } }],
    ['network error',       new Error('Network Error')],
  ])('transient failure (%s) keeps the token and does not announce unauthorized', async (_n, err) => {
    setToken('still-good')
    axiosPost.mockRejectedValue(err)
    const events = []
    const l = () => events.push(1)
    window.addEventListener('hygie:unauthorized', l)
    await expect(refreshOnce()).rejects.toBe(err)
    window.removeEventListener('hygie:unauthorized', l)
    expect(events.length).toBe(0)
    expect(getToken()).toBe('still-good')
  })

  it('isAuthFailure is true only for 401/403 responses', () => {
    expect(isAuthFailure({ response: { status: 401 } })).toBe(true)
    expect(isAuthFailure({ response: { status: 403 } })).toBe(true)
    expect(isAuthFailure({ response: { status: 502 } })).toBe(false)
    expect(isAuthFailure(new Error('x'))).toBe(false)
    expect(isAuthFailure(undefined)).toBe(false)
  })

  it('a refresh invalidated while in flight (logout) leaves no token and no event behind', async () => {
    let resolve
    axiosPost.mockReturnValue(new Promise(r => { resolve = r }))
    const seen = []
    const l = () => seen.push(1)
    window.addEventListener('hygie:token-refreshed', l)
    const p = refreshOnce()
    invalidateRefresh()
    resolve({ data: { access_token: 'late-token' } })
    await expect(p).rejects.toThrow()
    window.removeEventListener('hygie:token-refreshed', l)
    expect(seen.length).toBe(0)
    expect(getToken()).toBe('')
  })
})
