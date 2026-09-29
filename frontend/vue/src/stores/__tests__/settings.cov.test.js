// src/stores/__tests__/settings.cov.test.js
// Coverage for stores/settings.js (0% before this file).
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

import api from '@/api/client'
import { useSettingsStore } from '../settings'

describe('useSettingsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('initial state is an empty settings object, not loading', () => {
    const s = useSettingsStore()
    expect(s.settings).toEqual({})
    expect(s.loading).toBe(false)
  })

  it('fetch replaces settings with the API payload and toggles loading around the call', async () => {
    let loadingDuringCall = null
    api.get.mockImplementation(() => {
      loadingDuringCall = s.loading
      return Promise.resolve({ data: { retention_days: 30 } })
    })
    const s = useSettingsStore()
    const p = s.fetch()
    expect(s.loading).toBe(true)
    await p
    expect(loadingDuringCall).toBe(true)
    expect(s.settings).toEqual({ retention_days: 30 })
    expect(s.loading).toBe(false)
  })

  it('fetch resets loading to false even when the API call rejects, and propagates the error', async () => {
    api.get.mockRejectedValue(new Error('boom'))
    const s = useSettingsStore()
    await expect(s.fetch()).rejects.toThrow('boom')
    expect(s.loading).toBe(false)
  })

  it('fetch does not overwrite settings when the request fails', async () => {
    api.get.mockResolvedValueOnce({ data: { a: 1 } })
    const s = useSettingsStore()
    await s.fetch()
    expect(s.settings).toEqual({ a: 1 })

    api.get.mockRejectedValueOnce(new Error('boom'))
    await expect(s.fetch()).rejects.toThrow()
    expect(s.settings).toEqual({ a: 1 })
  })

  it('save posts the patch and merges it into existing settings (does not replace the object)', async () => {
    api.post.mockResolvedValue({ data: { status: 'ok' } })
    const s = useSettingsStore()
    s.settings = { retention_days: 30, theme: 'dark' }
    await s.save({ retention_days: 60 })
    expect(api.post).toHaveBeenCalledWith('/settings', { retention_days: 60 })
    expect(s.settings).toEqual({ retention_days: 60, theme: 'dark' })
  })

  it('save does not touch local settings when the API call rejects', async () => {
    api.post.mockRejectedValue(new Error('validation failed'))
    const s = useSettingsStore()
    s.settings = { retention_days: 30 }
    await expect(s.save({ retention_days: 60 })).rejects.toThrow('validation failed')
    expect(s.settings).toEqual({ retention_days: 30 })
  })
})
