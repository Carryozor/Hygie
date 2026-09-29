// src/stores/__tests__/auth.cov.test.js
// Gap-fill for stores/auth.js (87% before this file): setup(), fetchMe(),
// and the store-init branch that schedules a refresh when a token already
// exists in memory at construction time (e.g. a second store instance
// created mid-session, after login already happened elsewhere).
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

import api from '@/api/client'
import { getToken, setToken, clearToken } from '@/api/tokenStore'
import { useAuthStore } from '../auth'

describe('useAuthStore — gap fill', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    clearToken()
    vi.clearAllMocks()
  })
  afterEach(() => { localStorage.clear(); clearToken() })

  it('setup() stores the access token in memory only, like login()', async () => {
    api.post.mockResolvedValueOnce({
      data: { access_token: 'first-admin-token', username: 'admin' },
    })
    const store = useAuthStore()
    await store.setup('admin', 'pass')
    expect(api.post).toHaveBeenCalledWith('/auth/setup', { username: 'admin', password: 'pass' })
    expect(store.token).toBe('first-admin-token')
    expect(getToken()).toBe('first-admin-token')
    expect(store.username).toBe('admin')
    expect(localStorage.getItem('hygie_token')).toBeNull()
  })

  it('setup() falls back to the legacy "token" field and to the given username', async () => {
    api.post.mockResolvedValueOnce({ data: { token: 'legacy-tok' } })
    const store = useAuthStore()
    await store.setup('newadmin', 'pass')
    expect(store.token).toBe('legacy-tok')
    expect(store.username).toBe('newadmin')
  })

  it('fetchMe() populates username from the API when a token is present', async () => {
    setToken('tok')
    api.get.mockResolvedValueOnce({ data: { username: 'bob' } })
    const store = useAuthStore()
    await store.fetchMe()
    expect(api.get).toHaveBeenCalledWith('/auth/me')
    expect(store.username).toBe('bob')
  })

  it('fetchMe() does not call the API at all when there is no token', async () => {
    const store = useAuthStore()
    await store.fetchMe()
    expect(api.get).not.toHaveBeenCalled()
  })

  it('fetchMe() silently ignores an API failure — username stays whatever it was', async () => {
    setToken('tok')
    api.get.mockRejectedValueOnce(new Error('500'))
    const store = useAuthStore()
    await expect(store.fetchMe()).resolves.toBeUndefined()
    expect(store.username).toBe('')
  })

  it('schedules an auto-refresh at store construction when a token already exists in memory', async () => {
    vi.useFakeTimers()
    setToken('pre-existing-token')
    const store = useAuthStore()
    expect(store.token).toBe('pre-existing-token')
    // 55 minutes (ACCESS_TTL - REFRESH_BEFORE) must have a refresh scheduled —
    // advancing past it should trigger exactly one POST /auth/refresh.
    api.post.mockResolvedValue({ data: { access_token: 'renewed' } })
    await vi.advanceTimersByTimeAsync(55 * 60 * 1000 + 1)
    expect(api.post).toHaveBeenCalledWith('/auth/refresh', expect.any(Object))
    expect(store.token).toBe('renewed')
    vi.useRealTimers()
  })

  it('does not schedule any refresh at construction when no token exists', async () => {
    vi.useFakeTimers()
    const store = useAuthStore()
    expect(store.token).toBe('')
    await vi.advanceTimersByTimeAsync(60 * 60 * 1000)
    expect(api.post).not.toHaveBeenCalled()
    vi.useRealTimers()
  })

  it('clears the previous refresh timer instead of stacking a second one on a second login', async () => {
    vi.useFakeTimers()
    api.post.mockResolvedValue({ data: { access_token: 'a1', username: 'admin' } })
    const store = useAuthStore()
    await store.login('admin', 'pass') // schedules timer #1
    await store.login('admin', 'pass') // must clear #1, schedule #2 — not stack both
    api.post.mockClear()
    api.post.mockResolvedValue({ data: { access_token: 'renewed' } })
    await vi.advanceTimersByTimeAsync(55 * 60 * 1000 + 1)
    // If the first timer had not been cleared, refresh() would have fired twice.
    expect(api.post).toHaveBeenCalledTimes(1)
    vi.useRealTimers()
  })

  it('login falls back to the "u" parameter when the response has no username field', async () => {
    api.post.mockResolvedValueOnce({ data: { access_token: 'tok' } })
    const store = useAuthStore()
    await store.login('fallback-user', 'pass')
    expect(store.username).toBe('fallback-user')
  })

  it('refresh() falls back to the legacy "token" field when access_token is absent', async () => {
    api.post.mockResolvedValueOnce({ data: { token: 'legacy-refreshed' } })
    const store = useAuthStore()
    const ok = await store.refresh()
    expect(ok).toBe(true)
    expect(store.token).toBe('legacy-refreshed')
  })

  it('logout() sends the legacy refresh token from localStorage when present', async () => {
    localStorage.setItem('hygie_refresh_token', 'legacy-ref-123')
    api.post.mockResolvedValue({ data: { status: 'ok' } })
    const store = useAuthStore()
    await store.logout()
    expect(api.post).toHaveBeenCalledWith('/auth/logout', { refresh_token: 'legacy-ref-123' })
  })

  it('logout() sends an empty string when there is no legacy refresh token at all', async () => {
    expect(localStorage.getItem('hygie_refresh_token')).toBeNull()
    api.post.mockResolvedValue({ data: { status: 'ok' } })
    const store = useAuthStore()
    await store.logout()
    expect(api.post).toHaveBeenCalledWith('/auth/logout', { refresh_token: '' })
  })
})
