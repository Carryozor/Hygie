// src/stores/__tests__/auth.test.js
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

import api from '@/api/client'
import { useAuthStore } from '../auth'

describe('useAuthStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.clearAllMocks()
  })
  afterEach(() => { localStorage.clear() })

  it('isLoggedIn false when no token', () => {
    expect(useAuthStore().isLoggedIn).toBe(false)
  })

  it('isLoggedIn true when token in localStorage', () => {
    localStorage.setItem('hygie_token', 'tok')
    expect(useAuthStore().isLoggedIn).toBe(true)
  })

  it('login stores access_token and refresh_token', async () => {
    api.post.mockResolvedValueOnce({
      data: { access_token: 'acc', refresh_token: 'ref', username: 'admin' },
    })
    const store = useAuthStore()
    await store.login('admin', 'pass')
    expect(store.token).toBe('acc')
    expect(store.refreshToken).toBe('ref')
    expect(localStorage.getItem('hygie_token')).toBe('acc')
    expect(localStorage.getItem('hygie_refresh_token')).toBe('ref')
  })

  it('login falls back to token field for backward compat', async () => {
    api.post.mockResolvedValueOnce({ data: { token: 'legacy', username: 'admin' } })
    const store = useAuthStore()
    await store.login('admin', 'pass')
    expect(store.token).toBe('legacy')
  })

  it('logout clears tokens', async () => {
    api.post.mockResolvedValue({ data: { status: 'ok' } })
    localStorage.setItem('hygie_token', 'tok')
    localStorage.setItem('hygie_refresh_token', 'ref')
    const store = useAuthStore()
    await store.logout()
    expect(store.token).toBe('')
    expect(localStorage.getItem('hygie_token')).toBeNull()
    expect(localStorage.getItem('hygie_refresh_token')).toBeNull()
  })

  it('refresh calls /auth/refresh and updates token', async () => {
    api.post.mockResolvedValueOnce({ data: { access_token: 'new' } })
    const store = useAuthStore()
    store.refreshToken = 'my-ref'
    const ok = await store.refresh()
    expect(ok).toBe(true)
    expect(store.token).toBe('new')
    expect(api.post).toHaveBeenCalledWith('/auth/refresh', { refresh_token: 'my-ref' })
  })

  it('refresh emits unauthorized on failure', async () => {
    api.post.mockRejectedValueOnce(new Error('401'))
    const events = []
    window.addEventListener('hygie:unauthorized', () => events.push(1))
    const store = useAuthStore()
    store.refreshToken = 'bad'
    const ok = await store.refresh()
    expect(ok).toBe(false)
    expect(events.length).toBeGreaterThan(0)
  })

  it('checkSetup returns and stores setup_complete', async () => {
    api.get.mockResolvedValueOnce({ data: { setup_complete: true } })
    const store = useAuthStore()
    expect(await store.checkSetup()).toBe(true)
    expect(store.setupComplete).toBe(true)
  })
})
