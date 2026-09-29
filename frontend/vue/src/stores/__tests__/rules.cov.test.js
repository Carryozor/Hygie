// src/stores/__tests__/rules.cov.test.js
// Coverage for stores/rules.js (0% before this file). Rules drive the
// deletion pipeline, so CRUD state after success AND after failure matters:
// a rule that silently fails to delete but stays "deleted" in the UI is
// exactly the kind of false-positive this suite exists to prevent.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

import api from '@/api/client'
import { useRulesStore } from '../rules'

describe('useRulesStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  describe('fetchAll', () => {
    it('populates simpleRules and expertRules and clears loading/error on success', async () => {
      api.get.mockImplementation(url => {
        if (url === '/seerr-rules') return Promise.resolve({ data: [{ id: 1 }] })
        if (url === '/expert-rules') return Promise.resolve({ data: [{ id: 2 }] })
        throw new Error(`unexpected ${url}`)
      })
      const s = useRulesStore()
      await s.fetchAll()
      expect(s.simpleRules).toEqual([{ id: 1 }])
      expect(s.expertRules).toEqual([{ id: 2 }])
      expect(s.loading).toBe(false)
      expect(s.error).toBeNull()
    })

    it('degrades seerr-rules to [] when that endpoint alone fails (e.g. Seerr not configured)', async () => {
      api.get.mockImplementation(url => {
        if (url === '/seerr-rules') return Promise.reject(new Error('seerr down'))
        if (url === '/expert-rules') return Promise.resolve({ data: [{ id: 2 }] })
      })
      const s = useRulesStore()
      await s.fetchAll()
      expect(s.simpleRules).toEqual([])
      expect(s.expertRules).toEqual([{ id: 2 }])
      expect(s.error).toBeNull()
    })

    it('sets error and stops loading when expert-rules (uncaught) fails', async () => {
      api.get.mockImplementation(url => {
        if (url === '/seerr-rules') return Promise.resolve({ data: [] })
        if (url === '/expert-rules') return Promise.reject(new Error('db down'))
      })
      const s = useRulesStore()
      await s.fetchAll()
      expect(s.error).toBe('db down')
      expect(s.loading).toBe(false)
    })

    it('loading is true synchronously once fetchAll starts, then false once it settles', async () => {
      api.get.mockResolvedValue({ data: [] })
      const s = useRulesStore()
      const p = s.fetchAll()
      expect(s.loading).toBe(true)
      await p
      expect(s.loading).toBe(false)
    })

    it('defaults both lists to [] when the API responds with no data field at all', async () => {
      api.get.mockResolvedValue({ data: undefined })
      const s = useRulesStore()
      await s.fetchAll()
      expect(s.simpleRules).toEqual([])
      expect(s.expertRules).toEqual([])
    })

    it('falls back to a generic error message when the rejection has no .message', async () => {
      api.get.mockImplementation(url => {
        if (url === '/seerr-rules') return Promise.resolve({ data: [] })
        if (url === '/expert-rules') return Promise.reject({})
      })
      const s = useRulesStore()
      await s.fetchAll()
      expect(s.error).toBe('fetch error')
    })
  })

  describe('simple rules CRUD', () => {
    it('createSimpleRule posts the payload and appends the returned rule', async () => {
      api.post.mockResolvedValue({ data: { id: 5, name: 'x' } })
      const s = useRulesStore()
      const created = await s.createSimpleRule({ name: 'x' })
      expect(api.post).toHaveBeenCalledWith('/seerr-rules', { name: 'x' })
      expect(created).toEqual({ id: 5, name: 'x' })
      expect(s.simpleRules).toEqual([{ id: 5, name: 'x' }])
    })

    it('createSimpleRule does not touch state when the API rejects', async () => {
      api.post.mockRejectedValue(new Error('invalid'))
      const s = useRulesStore()
      await expect(s.createSimpleRule({ name: 'x' })).rejects.toThrow('invalid')
      expect(s.simpleRules).toEqual([])
    })

    it('updateSimpleRule replaces the matching entry in place', async () => {
      const s = useRulesStore()
      s.simpleRules = [{ id: 1, name: 'old' }, { id: 2, name: 'other' }]
      api.put.mockResolvedValue({ data: { id: 1, name: 'new' } })
      const updated = await s.updateSimpleRule(1, { name: 'new' })
      expect(api.put).toHaveBeenCalledWith('/seerr-rules/1', { name: 'new' })
      expect(updated).toEqual({ id: 1, name: 'new' })
      expect(s.simpleRules).toEqual([{ id: 1, name: 'new' }, { id: 2, name: 'other' }])
    })

    it('updateSimpleRule is a no-op on local state when the id is not found locally', async () => {
      const s = useRulesStore()
      s.simpleRules = [{ id: 2, name: 'other' }]
      api.put.mockResolvedValue({ data: { id: 1, name: 'new' } })
      await s.updateSimpleRule(1, { name: 'new' })
      expect(s.simpleRules).toEqual([{ id: 2, name: 'other' }])
    })

    it('updateSimpleRule leaves the list untouched when the API rejects', async () => {
      const s = useRulesStore()
      s.simpleRules = [{ id: 1, name: 'old' }]
      api.put.mockRejectedValue(new Error('422'))
      await expect(s.updateSimpleRule(1, { name: 'new' })).rejects.toThrow('422')
      expect(s.simpleRules).toEqual([{ id: 1, name: 'old' }])
    })

    it('deleteSimpleRule removes the rule locally only after the API call resolves', async () => {
      const s = useRulesStore()
      s.simpleRules = [{ id: 1 }, { id: 2 }]
      api.delete.mockResolvedValue({})
      await s.deleteSimpleRule(1)
      expect(api.delete).toHaveBeenCalledWith('/seerr-rules/1')
      expect(s.simpleRules).toEqual([{ id: 2 }])
    })

    it('deleteSimpleRule keeps the rule in state when the API call fails', async () => {
      const s = useRulesStore()
      s.simpleRules = [{ id: 1 }]
      api.delete.mockRejectedValue(new Error('server error'))
      await expect(s.deleteSimpleRule(1)).rejects.toThrow('server error')
      expect(s.simpleRules).toEqual([{ id: 1 }])
    })
  })

  describe('expert rules CRUD', () => {
    it('createExpertRule posts and appends the returned rule', async () => {
      api.post.mockResolvedValue({ data: { id: 9, enabled: true } })
      const s = useRulesStore()
      const created = await s.createExpertRule({ enabled: true })
      expect(api.post).toHaveBeenCalledWith('/expert-rules', { enabled: true })
      expect(created).toEqual({ id: 9, enabled: true })
      expect(s.expertRules).toEqual([{ id: 9, enabled: true }])
    })

    it('deleteExpertRule removes locally only on API success', async () => {
      const s = useRulesStore()
      s.expertRules = [{ id: 9 }]
      api.delete.mockResolvedValue({})
      await s.deleteExpertRule(9)
      expect(api.delete).toHaveBeenCalledWith('/expert-rules/9')
      expect(s.expertRules).toEqual([])
    })

    it('deleteExpertRule keeps state on API failure', async () => {
      const s = useRulesStore()
      s.expertRules = [{ id: 9 }]
      api.delete.mockRejectedValue(new Error('403'))
      await expect(s.deleteExpertRule(9)).rejects.toThrow('403')
      expect(s.expertRules).toEqual([{ id: 9 }])
    })

    it('toggleExpertRule flips enabled via updateExpertRule, preserving other fields', async () => {
      const s = useRulesStore()
      s.expertRules = [{ id: 3, enabled: true, name: 'r1' }]
      api.put.mockResolvedValue({ data: { id: 3, enabled: false, name: 'r1' } })
      await s.toggleExpertRule(3)
      expect(api.put).toHaveBeenCalledWith('/expert-rules/3', { id: 3, enabled: false, name: 'r1' })
      expect(s.expertRules).toEqual([{ id: 3, enabled: false, name: 'r1' }])
    })

    it('toggleExpertRule does nothing (no API call) when the rule id does not exist', async () => {
      const s = useRulesStore()
      s.expertRules = [{ id: 3, enabled: true }]
      await s.toggleExpertRule(999)
      expect(api.put).not.toHaveBeenCalled()
      expect(s.expertRules).toEqual([{ id: 3, enabled: true }])
    })

    it('updateExpertRule still calls the API but leaves local state untouched when the id is not found locally', async () => {
      const s = useRulesStore()
      s.expertRules = [{ id: 1, enabled: true }]
      api.put.mockResolvedValue({ data: { id: 2, enabled: false } })
      const updated = await s.updateExpertRule(2, { enabled: false })
      expect(updated).toEqual({ id: 2, enabled: false })
      expect(s.expertRules).toEqual([{ id: 1, enabled: true }])
    })
  })

  describe('migrateFromLibraries', () => {
    it('re-fetches rules when the migration created at least one rule', async () => {
      api.post.mockResolvedValueOnce({ data: { created: 2 } })
      api.get.mockImplementation(url => {
        if (url === '/seerr-rules') return Promise.resolve({ data: [] })
        if (url === '/expert-rules') return Promise.resolve({ data: [{ id: 'migrated' }] })
      })
      const s = useRulesStore()
      const n = await s.migrateFromLibraries()
      expect(n).toBe(2)
      expect(api.get).toHaveBeenCalled()
      expect(s.expertRules).toEqual([{ id: 'migrated' }])
    })

    it('does not re-fetch when nothing was created', async () => {
      api.post.mockResolvedValueOnce({ data: { created: 0 } })
      const s = useRulesStore()
      const n = await s.migrateFromLibraries()
      expect(n).toBe(0)
      expect(api.get).not.toHaveBeenCalled()
    })

    it('defaults to 0 created when the field is missing from the response', async () => {
      api.post.mockResolvedValueOnce({ data: {} })
      const s = useRulesStore()
      const n = await s.migrateFromLibraries()
      expect(n).toBe(0)
    })
  })

  describe('runScan', () => {
    it('posts to scan-multi with all ids when libraryIds has more than one entry', async () => {
      api.post.mockResolvedValue({})
      const s = useRulesStore()
      await s.runScan(null, ['a', 'b'])
      expect(api.post).toHaveBeenCalledWith('/libraries/scan-multi', { library_ids: ['a', 'b'] })
    })

    it('posts to the single-library scan endpoint when libraryIds has exactly one entry', async () => {
      api.post.mockResolvedValue({})
      const s = useRulesStore()
      await s.runScan(null, ['a'])
      expect(api.post).toHaveBeenCalledWith('/libraries/a/scan')
    })

    it('posts to the single-library scan endpoint using libraryId when libraryIds is absent', async () => {
      api.post.mockResolvedValue({})
      const s = useRulesStore()
      await s.runScan('lib-42')
      expect(api.post).toHaveBeenCalledWith('/libraries/lib-42/scan')
    })

    it('falls back to a global scan trigger when neither libraryId nor libraryIds is given', async () => {
      api.post.mockResolvedValue({})
      const s = useRulesStore()
      await s.runScan()
      expect(api.post).toHaveBeenCalledWith('/scan/trigger')
    })

    it('falls back to a global scan trigger when libraryIds is an empty array', async () => {
      api.post.mockResolvedValue({})
      const s = useRulesStore()
      await s.runScan(null, [])
      expect(api.post).toHaveBeenCalledWith('/scan/trigger')
    })
  })
})
