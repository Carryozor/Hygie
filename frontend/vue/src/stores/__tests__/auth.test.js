// src/stores/__tests__/auth.test.js
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const axiosPost = vi.hoisted(() => vi.fn())
// Token refresh goes through api/refreshCoordinator.js, which uses plain axios.
vi.mock('axios', () => ({ default: { post: axiosPost } }))

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

import api from '@/api/client'
import { getToken, setToken, clearToken } from '@/api/tokenStore'
import { useAuthStore } from '../auth'

describe('useAuthStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    clearToken()
    vi.clearAllMocks()
  })
  afterEach(() => { localStorage.clear(); clearToken() })

  it('isLoggedIn false when no token', () => {
    expect(useAuthStore().isLoggedIn).toBe(false)
  })

  it('isLoggedIn true when a token is already set in memory', () => {
    // The access token lives in tokenStore.js (in-memory), not localStorage —
    // see api/tokenStore.js for why (XSS can read localStorage, not module state).
    setToken('tok')
    expect(useAuthStore().isLoggedIn).toBe(true)
  })

  it('login stores access_token in memory only — never in localStorage', async () => {
    // The refresh token lives in an httpOnly cookie, and the access token
    // lives only in memory — localStorage would expose either to any XSS.
    api.post.mockResolvedValueOnce({
      data: { access_token: 'acc', refresh_token: 'ref', username: 'admin' },
    })
    const store = useAuthStore()
    await store.login('admin', 'pass')
    expect(store.token).toBe('acc')
    expect(getToken()).toBe('acc')
    expect(localStorage.getItem('hygie_token')).toBeNull()
    expect(localStorage.getItem('hygie_refresh_token')).toBeNull()
  })

  it('login falls back to token field for backward compat', async () => {
    api.post.mockResolvedValueOnce({ data: { token: 'legacy', username: 'admin' } })
    const store = useAuthStore()
    await store.login('admin', 'pass')
    expect(store.token).toBe('legacy')
  })

  it('logout clears tokens', async () => {
    api.post.mockResolvedValue({ data: { status: 'ok' } })
    setToken('tok')
    localStorage.setItem('hygie_refresh_token', 'ref')
    const store = useAuthStore()
    await store.logout()
    expect(store.token).toBe('')
    expect(getToken()).toBe('')
    expect(localStorage.getItem('hygie_refresh_token')).toBeNull()
  })

  it('refresh relies on the httpOnly cookie (empty body)', async () => {
    axiosPost.mockResolvedValueOnce({ data: { access_token: 'new' } })
    const store = useAuthStore()
    const ok = await store.refresh()
    expect(ok).toBe(true)
    expect(store.token).toBe('new')
    expect(axiosPost).toHaveBeenCalledWith('/api/auth/refresh', { refresh_token: '' })
  })

  it('refresh sends and purges a legacy localStorage refresh token', async () => {
    localStorage.setItem('hygie_refresh_token', 'legacy-ref')
    axiosPost.mockResolvedValueOnce({ data: { access_token: 'new' } })
    const store = useAuthStore()
    const ok = await store.refresh()
    expect(ok).toBe(true)
    expect(axiosPost).toHaveBeenCalledWith('/api/auth/refresh', { refresh_token: 'legacy-ref' })
    expect(localStorage.getItem('hygie_refresh_token')).toBeNull()
  })

  it('refresh emits unauthorized on failure', async () => {
    axiosPost.mockRejectedValueOnce({ response: { status: 401 } })
    const events = []
    window.addEventListener('hygie:unauthorized', () => events.push(1))
    const store = useAuthStore()
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
  describe('two-step login (MFA)', () => {
    const MFA = { data: { mfa_required: true, mfa_token: 'x'.repeat(43), expires_in: 300 } }

    it('login with mfa_required keeps no access token', async () => {
      api.post.mockResolvedValueOnce(MFA)
      const store = useAuthStore()
      const res = await store.login('admin', 'pass')
      expect(res).toEqual({ mfaRequired: true })
      expect(getToken()).toBeFalsy()
      expect(store.isLoggedIn).toBe(false)
      expect(store.pendingMfa).toBeTruthy()
    })

    it('login without 2FA still returns mfaRequired false and logs in', async () => {
      api.post.mockResolvedValueOnce({ data: { access_token: 'acc', username: 'admin' } })
      const store = useAuthStore()
      expect(await store.login('admin', 'pass')).toEqual({ mfaRequired: false })
      expect(store.isLoggedIn).toBe(true)
    })

    it('verifyMfa posts {mfa_token, code}, sets token and clears pending', async () => {
      api.post.mockResolvedValueOnce(MFA)
      const store = useAuthStore()
      await store.login('admin', 'pass')
      api.post.mockResolvedValueOnce({ data: { access_token: 'acc2', username: 'admin' } })
      await store.verifyMfa('123456')
      expect(api.post).toHaveBeenLastCalledWith('/auth/login/mfa', { mfa_token: 'x'.repeat(43), code: '123456' })
      expect(getToken()).toBe('acc2')
      expect(store.isLoggedIn).toBe(true)
      expect(store.pendingMfa).toBeNull()
    })

    it('verifyMfa keeps pending on a wrong code (mfa_code_invalid)', async () => {
      api.post.mockResolvedValueOnce(MFA)
      const store = useAuthStore()
      await store.login('admin', 'pass')
      api.post.mockRejectedValueOnce({ response: { status: 401, data: { code: 'mfa_code_invalid' } } })
      await expect(store.verifyMfa('000000')).rejects.toBeTruthy()
      expect(store.pendingMfa).toBeTruthy()
      expect(store.isLoggedIn).toBe(false)
    })

    it('challenge_invalid clears pending', async () => {
      api.post.mockResolvedValueOnce(MFA)
      const store = useAuthStore()
      await store.login('admin', 'pass')
      api.post.mockRejectedValueOnce({ response: { status: 401, data: { code: 'mfa_challenge_invalid' } } })
      await expect(store.verifyMfa('000000')).rejects.toBeTruthy()
      expect(store.pendingMfa).toBeNull()
    })

    it('verifyMfa without a pending challenge rejects without calling the API', async () => {
      const store = useAuthStore()
      await expect(store.verifyMfa('123456')).rejects.toBeTruthy()
      expect(api.post).not.toHaveBeenCalled()
    })

    it('cancelMfa drops the pending challenge', async () => {
      api.post.mockResolvedValueOnce(MFA)
      const store = useAuthStore()
      await store.login('admin', 'pass')
      store.cancelMfa()
      expect(store.pendingMfa).toBeNull()
    })

    it('pending token never written to storage', async () => {
      const spies = [
        vi.spyOn(Storage.prototype, 'setItem'),
      ]
      api.post.mockResolvedValueOnce(MFA)
      const store = useAuthStore()
      await store.login('admin', 'pass')
      api.post.mockResolvedValueOnce({ data: { access_token: 'acc2' } })
      await store.verifyMfa('123456')
      for (const spy of spies) {
        for (const [, v] of spy.mock.calls) expect(String(v)).not.toContain('x'.repeat(43))
        spy.mockRestore()
      }
      expect(JSON.stringify({ ...localStorage })).not.toContain('x'.repeat(43))
      expect(JSON.stringify({ ...sessionStorage })).not.toContain('x'.repeat(43))
    })

    it('adoptAccessToken installs a new access token', () => {
      const store = useAuthStore()
      store.adoptAccessToken('fresh')
      expect(getToken()).toBe('fresh')
      expect(store.isLoggedIn).toBe(true)
    })
  })
})
