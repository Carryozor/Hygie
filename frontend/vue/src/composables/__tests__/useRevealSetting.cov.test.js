// src/composables/__tests__/useRevealSetting.cov.test.js
// Coverage for composables/useRevealSetting.js (0% before this file).
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn() },
}))

import api from '@/api/client'
import { isMasked, revealSetting } from '../useRevealSetting'

describe('isMasked', () => {
  it('is true for the exact mask sentinel', () => {
    expect(isMasked('***')).toBe(true)
  })

  it('is false for a real value, even one containing asterisks', () => {
    expect(isMasked('a***b')).toBe(false)
    expect(isMasked('secret')).toBe(false)
  })

  it('is false for empty string, null, and undefined', () => {
    expect(isMasked('')).toBe(false)
    expect(isMasked(null)).toBe(false)
    expect(isMasked(undefined)).toBe(false)
  })
})

describe('revealSetting', () => {
  beforeEach(() => vi.clearAllMocks())

  it('GETs /settings/reveal/<key> and returns the unmasked value', async () => {
    api.get.mockResolvedValueOnce({ data: { value: 'sk-real-secret' } })
    const value = await revealSetting('emby_api_key')
    expect(api.get).toHaveBeenCalledWith('/settings/reveal/emby_api_key')
    expect(value).toBe('sk-real-secret')
  })

  it('propagates a rejection (e.g. 403 on an unauthorized reveal) to the caller', async () => {
    api.get.mockRejectedValueOnce({ response: { status: 403 } })
    await expect(revealSetting('emby_api_key')).rejects.toEqual({ response: { status: 403 } })
  })
})
