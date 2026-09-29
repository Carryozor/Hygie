// src/composables/__tests__/useQueueItems.cov.test.js
// Coverage for composables/useQueueItems.js (0% before this file). The
// composable calls useI18n() at setup time, so it must be invoked inside a
// mounted component's setup() — plain function-call testing throws
// "must be called within setup()". withSetup() below is the standard
// VueUse-style harness for that.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createApp } from 'vue'
import { createI18n } from 'vue-i18n'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn() },
}))

import api from '@/api/client'
import { useQueueItems } from '../useQueueItems'

const i18n = createI18n({
  legacy: false,
  locale: 'en', // a real BCP 47 tag — formatDate() feeds this straight into toLocaleDateString()
  messages: {
    en: {
      queue: { error: { loadFailed: 'LOAD_FAILED' } },
      status: { pending: 'PENDING', deleted: 'DELETED', error: 'ERROR_STATUS' },
      days: {
        exceeded: 'EXCEEDED',
        imminent: 'IMMINENT',
        inHours: 'IN_HOURS_{n}',
        tomorrow: 'TOMORROW',
        inDays: 'IN_DAYS_{n}',
      },
    },
  },
})

function withSetup() {
  let result
  const app = createApp({
    setup() {
      result = useQueueItems()
      return () => null
    },
  })
  app.use(i18n)
  app.mount(document.createElement('div'))
  return result
}

describe('useQueueItems', () => {
  const NOW = new Date('2026-06-15T12:00:00Z')

  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
  })
  afterEach(() => vi.useRealTimers())

  describe('load', () => {
    it('requests /media with pagination/sort params and stores items+total on success', async () => {
      api.get.mockResolvedValueOnce({ data: { items: [{ id: 1 }], total: 3 } })
      const q = withSetup()
      await q.load()
      expect(api.get).toHaveBeenCalledWith('/media', {
        params: { limit: 50, offset: 0, sort: 'delete_at', dir: 'asc' },
      })
      expect(q.items.value).toEqual([{ id: 1 }])
      expect(q.total.value).toBe(3)
      expect(q.loading.value).toBe(false)
      expect(q.error.value).toBe('')
    })

    it('includes status and search params only when set', async () => {
      api.get.mockResolvedValueOnce({ data: { items: [], total: 0 } })
      const q = withSetup()
      q.statusFilter.value = 'pending'
      q.search.value = 'inception'
      q.page.value = 3
      await q.load()
      expect(api.get).toHaveBeenCalledWith('/media', {
        params: { limit: 50, offset: 100, sort: 'delete_at', dir: 'asc', status: 'pending', search: 'inception' },
      })
    })

    it('falls back to a bare array response and derives total from its length', async () => {
      api.get.mockResolvedValueOnce({ data: [{ id: 1 }, { id: 2 }] })
      const q = withSetup()
      await q.load()
      expect(q.items.value).toEqual([{ id: 1 }, { id: 2 }])
      expect(q.total.value).toBe(2)
    })

    it('defaults items to [] and total to 0 when the response body is falsy (no throw)', async () => {
      api.get.mockResolvedValueOnce({ data: '' }) // falsy but property-accessible, unlike null/undefined
      const q = withSetup()
      await q.load()
      expect(q.items.value).toEqual([])
      expect(q.total.value).toBe(0)
      expect(q.error.value).toBe('') // confirms this took the normal path, not the catch branch
    })

    it('sets a translated error and stops loading on API failure, without throwing', async () => {
      api.get.mockRejectedValueOnce(new Error('network down'))
      const q = withSetup()
      await q.load()
      expect(q.error.value).toBe('LOAD_FAILED')
      expect(q.loading.value).toBe(false)
      expect(q.items.value).toEqual([])
    })

    it('loading is true synchronously once load() starts', async () => {
      api.get.mockResolvedValue({ data: { items: [], total: 0 } })
      const q = withSetup()
      const p = q.load()
      expect(q.loading.value).toBe(true)
      await p
    })
  })

  describe('setSort', () => {
    it('sets a new sort field with ascending direction and resets to page 1', async () => {
      api.get.mockResolvedValue({ data: { items: [], total: 0 } })
      const q = withSetup()
      q.page.value = 5
      q.setSort('title')
      expect(q.sort.value).toBe('title')
      expect(q.dir.value).toBe('asc')
      expect(q.page.value).toBe(1)
      await vi.waitFor(() => expect(api.get).toHaveBeenCalled())
    })

    it('toggles direction when sorting by the same field again', () => {
      api.get.mockResolvedValue({ data: { items: [], total: 0 } })
      const q = withSetup()
      expect(q.dir.value).toBe('asc')
      q.setSort('delete_at') // same as default sort field
      expect(q.dir.value).toBe('desc')
      q.setSort('delete_at')
      expect(q.dir.value).toBe('asc')
    })
  })

  describe('setFilter', () => {
    it('sets the status filter and resets to page 1 without triggering a reload', () => {
      const q = withSetup()
      q.page.value = 4
      q.setFilter('deleted')
      expect(q.statusFilter.value).toBe('deleted')
      expect(q.page.value).toBe(1)
      expect(api.get).not.toHaveBeenCalled()
    })
  })

  describe('totalPages / visiblePages', () => {
    it('totalPages is the ceiling of total/50', () => {
      const q = withSetup()
      q.total.value = 101
      expect(q.totalPages.value).toBe(3)
    })

    it('lists every page directly when there are 7 or fewer pages', () => {
      const q = withSetup()
      q.total.value = 5 * 50
      q.page.value = 3
      expect(q.visiblePages.value).toEqual([1, 2, 3, 4, 5])
    })

    it('adds a leading ellipsis and trailing ellipsis when far from both edges', () => {
      const q = withSetup()
      q.total.value = 10 * 50 // 10 pages
      q.page.value = 5
      expect(q.visiblePages.value).toEqual([1, '…', 3, 4, 5, 6, 7, '…', 10])
    })

    it('shows page 1 directly (no ellipsis) when the window start is exactly 2', () => {
      const q = withSetup()
      q.total.value = 10 * 50
      q.page.value = 4 // start = max(1, 4-2) = 2
      expect(q.visiblePages.value).toEqual([1, 2, 3, 4, 5, 6, '…', 10])
    })

    it('shows the last page directly (no ellipsis) when the window end is exactly n-1', () => {
      const q = withSetup()
      q.total.value = 10 * 50
      q.page.value = 7 // end = min(10, 9) = 9 = n-1
      expect(q.visiblePages.value).toEqual([1, '…', 5, 6, 7, 8, 9, 10])
    })

    it('omits the leading ellipsis when the window already starts at page 1', () => {
      const q = withSetup()
      q.total.value = 10 * 50
      q.page.value = 2 // start = max(1, 0) = 1
      expect(q.visiblePages.value).toEqual([1, 2, 3, 4, '…', 10])
    })

    it('omits the trailing ellipsis when the window already reaches the last page', () => {
      const q = withSetup()
      q.total.value = 10 * 50
      q.page.value = 9 // end = min(10, 11) = 10 = n
      expect(q.visiblePages.value).toEqual([1, '…', 7, 8, 9, 10])
    })
  })

  describe('daysRemaining', () => {
    it('returns null when there is no deleteAt', () => {
      expect(withSetup().daysRemaining(null)).toBeNull()
    })

    it('rounds up to whole days remaining', () => {
      const q = withSetup()
      const in2Days = new Date(NOW.getTime() + 2 * 24 * 60 * 60 * 1000 + 1000).toISOString()
      expect(q.daysRemaining(in2Days)).toBe(3) // ceil(2 days + 1s)
    })
  })

  describe('daysLabel', () => {
    it('shows the deleted-status label regardless of the date when status is deleted', () => {
      expect(withSetup().daysLabel(null, 'deleted')).toBe('DELETED')
    })

    it('maps each remainingBucket kind to its translation key', () => {
      const q = withSetup()
      const past = new Date(NOW.getTime() - 1000).toISOString()
      const in10min = new Date(NOW.getTime() + 10 * 60 * 1000).toISOString()
      const in5h = new Date(NOW.getTime() + 5 * 60 * 60 * 1000).toISOString()
      const in1day = new Date(NOW.getTime() + 24 * 60 * 60 * 1000).toISOString() // exactly one day: ceil(24h/24h) = 1
      const in5days = new Date(NOW.getTime() + 5 * 24 * 60 * 60 * 1000).toISOString()

      expect(q.daysLabel(past, 'pending')).toBe('EXCEEDED')
      expect(q.daysLabel(in10min, 'pending')).toBe('IMMINENT')
      expect(q.daysLabel(in5h, 'pending')).toBe('IN_HOURS_5')
      expect(q.daysLabel(in1day, 'pending')).toBe('TOMORROW')
      expect(q.daysLabel(in5days, 'pending')).toBe('IN_DAYS_5')
    })

    it('shows an em-dash for the "none" bucket (no deleteAt, not deleted)', () => {
      expect(withSetup().daysLabel(null, 'pending')).toBe('—')
    })
  })

  describe('daysClass', () => {
    it('returns the muted class for a deleted item', () => {
      expect(withSetup().daysClass(null, 'deleted')).toBe('text-[var(--muted)]')
    })

    it('returns the muted class when there is no deleteAt', () => {
      expect(withSetup().daysClass(null, 'pending')).toBe('text-[var(--muted)]')
    })

    it('escalates red -> orange -> yellow -> muted as the deadline recedes', () => {
      const q = withSetup()
      const at = days => new Date(NOW.getTime() + days * 24 * 60 * 60 * 1000).toISOString()
      expect(q.daysClass(at(1), 'pending')).toBe('text-red-400')
      expect(q.daysClass(at(5), 'pending')).toBe('text-orange-400')
      expect(q.daysClass(at(10), 'pending')).toBe('text-yellow-400')
      expect(q.daysClass(at(30), 'pending')).toBe('text-[var(--muted)]')
    })
  })

  describe('gridBannerClass', () => {
    it('prioritises the deleted/error status banner over the date', () => {
      const q = withSetup()
      expect(q.gridBannerClass(null, 'deleted')).toBe('bg-green-600/90')
      expect(q.gridBannerClass(null, 'error')).toBe('bg-red-700/90')
    })

    it('falls back to the neutral banner when there is no deleteAt', () => {
      expect(withSetup().gridBannerClass(null, 'pending')).toBe('bg-[var(--bg3)]/80')
    })

    it('escalates red -> orange -> yellow -> black as the deadline recedes', () => {
      const q = withSetup()
      const at = days => new Date(NOW.getTime() + days * 24 * 60 * 60 * 1000).toISOString()
      expect(q.gridBannerClass(at(1), 'pending')).toBe('bg-red-600/90')
      expect(q.gridBannerClass(at(5), 'pending')).toBe('bg-orange-600/90')
      expect(q.gridBannerClass(at(10), 'pending')).toBe('bg-yellow-600/90')
      expect(q.gridBannerClass(at(30), 'pending')).toBe('bg-black/50')
    })
  })

  describe('rowUrgencyClass', () => {
    it('is empty for anything but pending status', () => {
      const q = withSetup()
      const soon = new Date(NOW.getTime() + 24 * 60 * 60 * 1000).toISOString()
      expect(q.rowUrgencyClass(soon, 'deleted')).toBe('')
      expect(q.rowUrgencyClass(soon, 'error')).toBe('')
    })

    it('is empty when there is no deleteAt', () => {
      expect(withSetup().rowUrgencyClass(null, 'pending')).toBe('')
    })

    it('highlights red within 3 days, orange within 7, and nothing beyond', () => {
      const q = withSetup()
      const at = days => new Date(NOW.getTime() + days * 24 * 60 * 60 * 1000).toISOString()
      expect(q.rowUrgencyClass(at(1), 'pending')).toBe('bg-red-500/5')
      expect(q.rowUrgencyClass(at(5), 'pending')).toBe('bg-orange-500/5')
      expect(q.rowUrgencyClass(at(30), 'pending')).toBe('')
    })
  })

  describe('formatDate', () => {
    it('returns an empty string for a falsy value', () => {
      expect(withSetup().formatDate('')).toBe('')
      expect(withSetup().formatDate(null)).toBe('')
    })

    it('formats a valid ISO date as dd/mm/yyyy-shaped output', () => {
      const out = withSetup().formatDate('2026-03-05T10:00:00Z')
      expect(out).toMatch(/\d{2}.\d{2}.\d{4}/)
    })
  })

  describe('statusLabel / statusClass', () => {
    it('maps known statuses to their translated labels', () => {
      const q = withSetup()
      expect(q.statusLabel('pending')).toBe('PENDING')
      expect(q.statusLabel('deleted')).toBe('DELETED')
      expect(q.statusLabel('error')).toBe('ERROR_STATUS')
    })

    it('returns the raw value for an unknown status', () => {
      expect(withSetup().statusLabel('mystery')).toBe('mystery')
    })

    it('maps known statuses to their pill classes and falls back for unknown ones', () => {
      const q = withSetup()
      expect(q.statusClass('pending')).toBe('bg-yellow-500/20 text-yellow-400')
      expect(q.statusClass('deleted')).toBe('bg-green-500/20 text-green-400')
      expect(q.statusClass('error')).toBe('bg-red-700/20 text-red-300')
      expect(q.statusClass('mystery')).toBe('bg-[var(--bg3)] text-[var(--muted)]')
    })
  })

  describe('isSeries', () => {
    it('is true for Episode, Series, and Season', () => {
      const q = withSetup()
      expect(q.isSeries('Episode')).toBe(true)
      expect(q.isSeries('Series')).toBe(true)
      expect(q.isSeries('Season')).toBe(true)
    })

    it('is false for Movie and other types', () => {
      const q = withSetup()
      expect(q.isSeries('Movie')).toBe(false)
      expect(q.isSeries(undefined)).toBe(false)
    })
  })
})
