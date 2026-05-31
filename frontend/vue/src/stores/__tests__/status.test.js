// src/stores/__tests__/status.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

vi.mock('@/stores/servers', () => ({
  useServersStore: () => ({
    servers: [{ id: '0', enabled: true }],
    fetch:   vi.fn().mockResolvedValue(undefined),
  }),
}))

import api from '@/api/client'
import { useStatusStore } from '../status'

describe('useStatusStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    localStorage.setItem('hygie_token', 'test')
  })

  it('initial state defaults', () => {
    const s = useStatusStore()
    expect(s.scanRunning).toBe(false)
    expect(s.deletionRunning).toBe(false)
    expect(s.hasUnseenErrors).toBe(false)
    expect(s.serverStatus).toBe('none')
    expect(s.logoStatus).toBe('none')
  })

  it('logoStatus is "error" when hasUnseenErrors', () => {
    const s = useStatusStore()
    s.hasUnseenErrors = true
    expect(s.logoStatus).toBe('error')
  })

  it('logoStatus is "ok" when serverStatus=ok and no errors', () => {
    const s = useStatusStore()
    s.serverStatus = 'ok'
    expect(s.logoStatus).toBe('ok')
  })

  it('logoStatus is "unknown" when serverStatus=unknown', () => {
    const s = useStatusStore()
    s.serverStatus = 'unknown'
    expect(s.logoStatus).toBe('unknown')
  })

  it('logoStatus is "unknown" (not "error") when serverStatus=error but no unseen logs', () => {
    const s = useStatusStore()
    s.serverStatus = 'error'
    s.hasUnseenErrors = false
    expect(s.logoStatus).toBe('unknown')
  })

  it('fetchScheduler updates scanRunning and scanNext', async () => {
    api.get.mockResolvedValueOnce({
      data: [
        { id: 'scan_job',     next_run: '2026-06-01T10:00:00Z', is_running: true  },
        { id: 'deletion_job', next_run: '2026-06-01T11:00:00Z', is_running: false },
      ],
    })
    const s = useStatusStore()
    await s.fetchScheduler()
    expect(s.scanRunning).toBe(true)
    expect(s.deletionRunning).toBe(false)
    expect(s.scanNext).toBe('2026-06-01T10:00:00Z')
  })

  it('checkUnseenErrors sets true when count > 0', async () => {
    api.get.mockResolvedValueOnce({ data: { count: 3 } })
    const s = useStatusStore()
    await s.checkUnseenErrors()
    expect(s.hasUnseenErrors).toBe(true)
  })

  it('checkUnseenErrors sets false when count = 0', async () => {
    api.get.mockResolvedValueOnce({ data: { count: 0 } })
    const s = useStatusStore()
    s.hasUnseenErrors = true
    await s.checkUnseenErrors()
    expect(s.hasUnseenErrors).toBe(false)
  })

  it('stop does not throw when never started', () => {
    expect(() => useStatusStore().stop()).not.toThrow()
  })
})
