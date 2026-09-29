// src/stores/__tests__/status.cov.test.js
// Gap-fill for stores/status.js (78% before this file): the scheduler-poll
// reschedule branch, checkServerHealth's empty/success/multi-server paths,
// and the Page Visibility handler (never called by the existing test file).
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

const serversState = vi.hoisted(() => ({ servers: [] }))
const serversFetch = vi.fn().mockResolvedValue(undefined)
vi.mock('@/stores/servers', () => ({
  useServersStore: () => ({
    get servers() { return serversState.servers },
    fetch: serversFetch,
  }),
}))

vi.mock('@/stores/auth', () => ({
  useAuthStore: vi.fn(() => ({ isLoggedIn: true })),
}))

import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useStatusStore } from '../status'

describe('useStatusStore — gap fill', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.useFakeTimers()
    serversState.servers = []
    useAuthStore.mockReturnValue({ isLoggedIn: true })
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  describe('checkServerHealth', () => {
    it('sets serverStatus to "none" and clears results when there are no enabled servers', async () => {
      serversState.servers = [{ id: '1', enabled: false }]
      const s = useStatusStore()
      s.serverStatus = 'ok'
      s.serverResults = [{ ok: true }]
      await s.checkServerHealth()
      expect(s.serverStatus).toBe('none')
      expect(s.serverError).toBe(false)
      expect(s.serverResults).toEqual([])
      expect(api.post).not.toHaveBeenCalled()
    })

    it('sets serverStatus to "ok" when every enabled server responds ok', async () => {
      serversState.servers = [{ id: '1', enabled: true, type: 'emby' }]
      api.post.mockResolvedValueOnce({ data: { ok: true } })
      const s = useStatusStore()
      await s.checkServerHealth()
      expect(api.post).toHaveBeenCalledWith('/settings/media-servers/1/test')
      expect(s.serverStatus).toBe('ok')
      expect(s.serverError).toBe(false)
      expect(s.serverResults).toEqual([{ ok: true, type: 'emby' }])
    })

    it('sets serverStatus to "unknown" when some (not all) servers fail', async () => {
      serversState.servers = [
        { id: '1', enabled: true, type: 'emby' },
        { id: '2', enabled: true, type: 'plex' },
      ]
      api.post
        .mockResolvedValueOnce({ data: { ok: true } })
        .mockRejectedValueOnce(new Error('timeout'))
      const s = useStatusStore()
      await s.checkServerHealth()
      expect(s.serverStatus).toBe('unknown')
      expect(s.serverError).toBe(true)
      expect(s.serverResults).toEqual([{ ok: true, type: 'emby' }, { ok: false, type: 'plex' }])
    })

    it('sets serverStatus to "error" when every server fails', async () => {
      serversState.servers = [{ id: '1', enabled: true, type: 'emby' }]
      api.post.mockRejectedValueOnce(new Error('down'))
      const s = useStatusStore()
      await s.checkServerHealth()
      expect(s.serverStatus).toBe('error')
      expect(s.serverError).toBe(true)
    })

    it('treats a resolved-but-falsy ok field as a failure', async () => {
      serversState.servers = [{ id: '1', enabled: true }]
      api.post.mockResolvedValueOnce({ data: { ok: false } })
      const s = useStatusStore()
      await s.checkServerHealth()
      expect(s.serverStatus).toBe('error')
      expect(s.serverResults).toEqual([{ ok: false, type: 'emby' }])
    })

    it('caps serverResults at 3 entries even with more enabled servers', async () => {
      serversState.servers = [
        { id: '1', enabled: true }, { id: '2', enabled: true },
        { id: '3', enabled: true }, { id: '4', enabled: true },
      ]
      api.post.mockResolvedValue({ data: { ok: true } })
      const s = useStatusStore()
      await s.checkServerHealth()
      expect(s.serverResults.length).toBe(3)
    })

    it('ignores servers with id === undefined or enabled === false', async () => {
      serversState.servers = [
        { id: undefined, enabled: true },
        { id: '1', enabled: false },
        { id: '2', enabled: true },
      ]
      api.post.mockResolvedValueOnce({ data: { ok: true } })
      const s = useStatusStore()
      await s.checkServerHealth()
      expect(api.post).toHaveBeenCalledTimes(1)
      expect(api.post).toHaveBeenCalledWith('/settings/media-servers/2/test')
    })
  })

  describe('fetchScheduler reschedule', () => {
    it('re-arms its own interval at 3s cadence while a job is running, once already started', async () => {
      api.get.mockResolvedValue({ data: [] }) // scheduler + unseen-errors for start()
      const s = useStatusStore()
      await s.start() // creates the initial 30s interval

      api.get.mockResolvedValueOnce({
        data: [{ id: 'scan_job', next_run: null, is_running: true }],
      })
      await s.fetchScheduler() // now reschedules at 3000ms since a job is running

      api.get.mockClear()
      api.get.mockResolvedValue({ data: [] })
      await vi.advanceTimersByTimeAsync(3000)
      expect(api.get).toHaveBeenCalledWith('/scheduler/status')
      s.stop() // avoid leaking this document-level listener into later tests
    })
  })

  describe('_onVisibilityChange (via document visibilitychange after start())', () => {
    function setHidden(hidden) {
      Object.defineProperty(document, 'hidden', { value: hidden, configurable: true })
    }

    afterEach(() => setHidden(false))

    it('suspends all polling intervals when the tab becomes hidden', async () => {
      api.get.mockResolvedValue({ data: [] })
      const s = useStatusStore()
      await s.start()
      api.get.mockClear()

      setHidden(true)
      document.dispatchEvent(new Event('visibilitychange'))

      await vi.advanceTimersByTimeAsync(120000)
      expect(api.get).not.toHaveBeenCalled()
      s.stop() // avoid leaking this document-level listener into later tests
    })

    it('immediately refreshes and resumes polling when the tab becomes visible again', async () => {
      api.get.mockResolvedValue({ data: [] })
      const s = useStatusStore()
      await s.start()

      setHidden(true)
      document.dispatchEvent(new Event('visibilitychange'))
      api.get.mockClear()

      setHidden(false)
      document.dispatchEvent(new Event('visibilitychange'))
      await vi.waitFor(() => expect(api.get).toHaveBeenCalled())
      expect(api.get).toHaveBeenCalledWith('/scheduler/status')
      expect(api.get).toHaveBeenCalledWith('/logs/unseen-errors-count')
      s.stop() // avoid leaking this document-level listener into later tests
    })

    it('stop() removes the visibilitychange listener so a later tab-hide no longer polls', async () => {
      api.get.mockResolvedValue({ data: [] })
      const s = useStatusStore()
      await s.start()
      s.stop()
      api.get.mockClear()

      setHidden(false)
      document.dispatchEvent(new Event('visibilitychange'))
      await vi.advanceTimersByTimeAsync(1000)
      expect(api.get).not.toHaveBeenCalled()
    })
  })

  describe('start() duplicate-call guard', () => {
    it('does not create a second set of intervals on a second start() call', async () => {
      api.get.mockResolvedValue({ data: [] })
      const s = useStatusStore()
      await s.start()
      serversFetch.mockClear()
      await s.start()
      expect(serversFetch).not.toHaveBeenCalled()
      s.stop() // avoid leaking this document-level listener into later tests
    })
  })
})
