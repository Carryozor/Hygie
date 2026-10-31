// Store <-> refresh coordinator integration and logout hardening.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const axiosPost = vi.hoisted(() => vi.fn())
vi.mock('axios', () => ({ default: { post: axiosPost } }))
vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), defaults: { headers: { common: {} } } },
}))

import api from '@/api/client'
import { refreshOnce } from '@/api/refreshCoordinator'
import { getToken, setToken, clearToken } from '@/api/tokenStore'
import { useAuthStore } from '../auth'

describe('auth store hardening', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    clearToken()
    vi.clearAllMocks()
    axiosPost.mockReset()
  })
  afterEach(() => { vi.useRealTimers() })

  it('store.refresh() and an interceptor-style refreshOnce() share ONE POST', async () => {
    let resolve
    axiosPost.mockReturnValue(new Promise(r => { resolve = r }))
    const store = useAuthStore()
    const fromGuard = store.refresh()
    const fromInterceptor = refreshOnce()
    resolve({ data: { access_token: 'shared' } })
    await Promise.all([fromGuard, fromInterceptor])
    expect(axiosPost).toHaveBeenCalledTimes(1)
    expect(store.token).toBe('shared')
    expect(store.isLoggedIn).toBe(true)
  })

  it('a refresh done by the interceptor (not the store) updates the store and reschedules the 55 min timer', async () => {
    vi.useFakeTimers()
    axiosPost.mockResolvedValue({ data: { access_token: 'via-interceptor' } })
    const store = useAuthStore()
    await refreshOnce()
    expect(store.token).toBe('via-interceptor')
    axiosPost.mockClear()
    axiosPost.mockResolvedValue({ data: { access_token: 'timer-renewed' } })
    await vi.advanceTimersByTimeAsync(55 * 60 * 1000 + 1)
    expect(axiosPost).toHaveBeenCalledTimes(1)
    expect(store.token).toBe('timer-renewed')
  })

  it('a failed refresh from the interceptor empties the store AND tokenStore', async () => {
    const store = useAuthStore()
    store.token = 'old'
    setToken('old')
    axiosPost.mockRejectedValue({ response: { status: 401 } })
    await expect(refreshOnce()).rejects.toBeTruthy()
    expect(store.token).toBe('')
    expect(store.isLoggedIn).toBe(false)
    expect(getToken()).toBe('')
  })

  it('store.refresh() resolves false (and logs out) when the shared refresh fails', async () => {
    axiosPost.mockRejectedValue({ response: { status: 401 } })
    const store = useAuthStore()
    expect(await store.refresh()).toBe(false)
    expect(store.isLoggedIn).toBe(false)
  })

  it('logout clears local state even when the server call fails, and drops the stale default header', async () => {
    api.post.mockRejectedValue(new Error('401 expired'))
    api.defaults.headers.common.Authorization = 'Bearer stale'
    setToken('tok')
    const store = useAuthStore()
    store.token = 'tok'
    await store.logout()
    expect(store.isLoggedIn).toBe(false)
    expect(getToken()).toBe('')
    expect(api.defaults.headers.common.Authorization).toBeUndefined()
  })

  // ── Review fixes ──────────────────────────────────────────────────────────
  it('a transient refresh failure (502) keeps the session and retries instead of logging out', async () => {
    vi.useFakeTimers()
    const store = useAuthStore()
    axiosPost.mockResolvedValueOnce({ data: { access_token: 'tok-ok' } })
    await store.refresh()
    expect(store.isLoggedIn).toBe(true)

    axiosPost.mockRejectedValueOnce({ response: { status: 502 } })
    expect(await store.refresh()).toBe(false)
    expect(store.isLoggedIn).toBe(true)            // NOT cleared
    expect(getToken()).toBe('tok-ok')

    axiosPost.mockResolvedValueOnce({ data: { access_token: 'tok-retry' } })
    await vi.advanceTimersByTimeAsync(61 * 1000)   // retry scheduled ~60 s later
    expect(store.token).toBe('tok-retry')
  })

  it('a 401 on refresh clears the session', async () => {
    const store = useAuthStore()
    setToken('x')
    axiosPost.mockRejectedValue({ response: { status: 401 } })
    expect(await store.refresh()).toBe(false)
    expect(store.isLoggedIn).toBe(false)
    expect(getToken()).toBe('')
  })

  it('logout during an in-flight refresh does not resurrect the session', async () => {
    const store = useAuthStore()
    setToken('before')
    store.token = 'before'
    let resolve
    axiosPost.mockReturnValue(new Promise(r => { resolve = r }))
    api.post.mockResolvedValue({ data: {} })
    const inflight = store.refresh()
    await store.logout()
    resolve({ data: { access_token: 'late' } })
    await inflight
    expect(store.isLoggedIn).toBe(false)
    expect(getToken()).toBe('')
  })
})
