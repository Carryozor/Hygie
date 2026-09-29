// src/stores/__tests__/stats.cov.test.js
// Coverage for stores/stats.js (0% before this file).
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn() },
}))

import api from '@/api/client'
import { useStatsStore } from '../stats'

describe('useStatsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('initial state has the documented default shapes', () => {
    const s = useStatsStore()
    expect(s.global).toEqual({ total_deleted: 0, total_ignored: 0, total_scans: 0, queue: {}, by_month: [] })
    expect(s.storage).toEqual({ disks: [], movies: {}, series: {}, total_media_size: 0, queue: {} })
    expect(s.error).toBeNull()
  })

  it('fetchGlobal replaces global with the API payload on success', async () => {
    api.get.mockResolvedValueOnce({ data: { total_deleted: 12, total_ignored: 3, total_scans: 5, queue: {}, by_month: [{ m: '2026-01', n: 2 }] } })
    const s = useStatsStore()
    await s.fetchGlobal()
    expect(api.get).toHaveBeenCalledWith('/stats/global')
    expect(s.global.total_deleted).toBe(12)
    expect(s.global.by_month).toEqual([{ m: '2026-01', n: 2 }])
    expect(s.error).toBeNull()
  })

  it('fetchGlobal sets error and keeps the previous global stats on failure', async () => {
    const s = useStatsStore()
    s.global = { total_deleted: 99, total_ignored: 0, total_scans: 0, queue: {}, by_month: [] }
    api.get.mockRejectedValueOnce(new Error('db down'))
    await s.fetchGlobal()
    expect(s.error).toBe('db down')
    expect(s.global.total_deleted).toBe(99)
  })

  it('fetchStorage replaces storage with the API payload on success', async () => {
    api.get.mockResolvedValueOnce({ data: { disks: [{ path: '/data', free: 100 }], movies: {}, series: {}, total_media_size: 500, queue: {} } })
    const s = useStatsStore()
    await s.fetchStorage()
    expect(api.get).toHaveBeenCalledWith('/storage')
    expect(s.storage.disks).toEqual([{ path: '/data', free: 100 }])
    expect(s.error).toBeNull()
  })

  it('fetchStorage sets error and keeps previous storage stats on failure', async () => {
    const s = useStatsStore()
    s.storage = { disks: [{ path: '/keep' }], movies: {}, series: {}, total_media_size: 1, queue: {} }
    api.get.mockRejectedValueOnce(new Error('timeout'))
    await s.fetchStorage()
    expect(s.error).toBe('timeout')
    expect(s.storage.disks).toEqual([{ path: '/keep' }])
  })

  it('fetchGlobal and fetchStorage fall back to a generic error message without .message', async () => {
    const s = useStatsStore()
    api.get.mockRejectedValueOnce({})
    await s.fetchGlobal()
    expect(s.error).toBe('fetch error')

    api.get.mockRejectedValueOnce({})
    await s.fetchStorage()
    expect(s.error).toBe('fetch error')
  })

  it('a successful fetchGlobal call clears a previously set error', async () => {
    const s = useStatsStore()
    api.get.mockRejectedValueOnce(new Error('fail once'))
    await s.fetchGlobal()
    expect(s.error).toBe('fail once')

    api.get.mockResolvedValueOnce({ data: { total_deleted: 1, total_ignored: 0, total_scans: 0, queue: {}, by_month: [] } })
    await s.fetchGlobal()
    expect(s.error).toBeNull()
  })
})
