// src/views/__tests__/CalendarView.cov.test.js
//
// CalendarView is read-only (admin view of the internal /calendar feed, not
// the public one — PublicView covers the security-sensitive duplicate
// logic). Coverage targets loading state, day selection/toggle, month nav,
// and the safeUrl guard on seerr_request_url.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mountView, flushAll } from './testUtils.cov.js'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn() },
}))

import api from '@/api/client'
import CalendarView from '../CalendarView.vue'

function todayStr() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

describe('CalendarView', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows the loading spinner while /calendar has not resolved', async () => {
    let resolve
    api.get.mockReturnValue(new Promise(r => { resolve = r }))
    const { wrapper } = await mountView(CalendarView, { path: '/calendar' })
    expect(wrapper.find('.fa-spinner').exists()).toBe(true)
    resolve({ data: { events: {} } })
    await flushAll()
  })

  it('silently shows an empty calendar when /calendar fails (no error banner)', async () => {
    api.get.mockRejectedValue({ response: { status: 500 } })
    const { wrapper } = await mountView(CalendarView, { path: '/calendar' })
    expect(wrapper.find('.fa-spinner').exists()).toBe(false)
    expect(wrapper.text()).toContain('0') // "0 planifié ce mois" summary
  })

  it('opens the day panel on click, and toggles it closed on a second click of the same day', async () => {
    const day = todayStr()
    api.get.mockResolvedValue({
      data: { events: { [day]: [{ id: 1, title: 'Dune', media_type: 'Movie', library_name: 'Films', delete_at: day + 'T12:00:00Z' }] } },
    })
    const { wrapper } = await mountView(CalendarView, { path: '/calendar' })
    const cell = wrapper.findAll('.min-h-\\[80px\\]').find(c => c.text().includes('Dune'))
    await cell.trigger('click')
    expect(wrapper.findAll('.divide-y.divide-\\[var\\(--border\\)\\]').length).toBeGreaterThan(0)
    await cell.trigger('click') // same day again -> closes
    expect(wrapper.find('.divide-y.divide-\\[var\\(--border\\)\\] .flex.items-center.gap-4').exists()).toBe(false)
  })

  it('clicking an empty day cell does nothing (no panel opens)', async () => {
    api.get.mockResolvedValue({ data: { events: {} } })
    const { wrapper } = await mountView(CalendarView, { path: '/calendar' })
    const cells = wrapper.findAll('.min-h-\\[80px\\]')
    await cells[10].trigger('click')
    expect(wrapper.find('.flex.items-center.gap-4.px-5.py-3').exists()).toBe(false)
  })

  it('navigates to the next and previous month, and the "today" shortcut only shows away from the current month', async () => {
    api.get.mockResolvedValue({ data: { events: {} } })
    const { wrapper } = await mountView(CalendarView, { path: '/calendar' })
    expect([...wrapper.findAll('button')].some(b => b.text() === "Aujourd'hui")).toBe(false)

    const [prevBtn, nextBtn] = wrapper.findAll('.flex.items-center.justify-between > button')
    await nextBtn.trigger('click')
    await flushAll()
    const todayBtn = [...wrapper.findAll('button')].find(b => b.text() === "Aujourd'hui")
    expect(todayBtn).toBeTruthy()

    await todayBtn.trigger('click')
    expect([...wrapper.findAll('button')].some(b => b.text() === "Aujourd'hui")).toBe(false)

    await prevBtn.trigger('click')
    await flushAll()
    expect([...wrapper.findAll('button')].some(b => b.text() === "Aujourd'hui")).toBe(true)
  })

  it('neutralizes a javascript: seerr_request_url in the day panel instead of linking it live', async () => {
    const day = todayStr()
    api.get.mockResolvedValue({
      data: {
        events: {
          [day]: [{ id: 1, title: 'Evil', media_type: 'Movie', library_name: 'Films', seerr_request_url: 'javascript:alert(1)' }],
        },
      },
    })
    const { wrapper } = await mountView(CalendarView, { path: '/calendar' })
    const cell = wrapper.findAll('.min-h-\\[80px\\]').find(c => c.text().includes('Evil'))
    await cell.trigger('click')
    const link = wrapper.find('a[href]')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toBe('#')
  })

  it('shows the imminent/tomorrow deletion labels per item in the day panel', async () => {
    const day = todayStr()
    const iso = offsetDays => new Date(Date.now() + offsetDays * 86400000).toISOString()
    api.get.mockResolvedValue({
      data: {
        events: {
          [day]: [
            { id: 1, title: 'ImminentOne', media_type: 'Movie', library_name: 'Films', delete_at: iso(0) },
            { id: 2, title: 'TomorrowOne', media_type: 'Movie', library_name: 'Films', delete_at: iso(1) },
          ],
        },
      },
    })
    const { wrapper } = await mountView(CalendarView, { path: '/calendar' })
    const cell = wrapper.findAll('.min-h-\\[80px\\]').find(c => c.text().includes('ImminentOne'))
    await cell.trigger('click')
    expect(wrapper.text()).toContain('imminent')
    expect(wrapper.text()).toContain('Demain')
  })

  it('uses the default (accent) chip color for a day more than 7 days out', async () => {
    // eventChipClass is keyed off the CELL's own date (daysFromToday(cell)),
    // not each item's delete_at — so the far-out event needs its own day cell,
    // safely inside next month's grid (mid-month, never within 7 days of today).
    const now = new Date()
    const nextMonth = new Date(now.getFullYear(), now.getMonth() + 1, 15)
    const farDay = `${nextMonth.getFullYear()}-${String(nextMonth.getMonth() + 1).padStart(2, '0')}-15`
    api.get.mockResolvedValue({
      data: { events: { [farDay]: [{ id: 3, title: 'FarOutOne', media_type: 'Movie', library_name: 'Films' }] } },
    })
    const { wrapper } = await mountView(CalendarView, { path: '/calendar' })
    const [, nextBtn] = wrapper.findAll('.flex.items-center.justify-between > button')
    await nextBtn.trigger('click')
    await flushAll()
    const chip = wrapper.findAll('div.truncate').find(d => d.text() === 'FarOutOne')
    expect(chip.classes()).toContain('bg-[var(--accent)]/20')
  })

  it('re-fetches /calendar when navigating to a different month', async () => {
    api.get.mockResolvedValue({ data: { events: {} } })
    const { wrapper } = await mountView(CalendarView, { path: '/calendar' })
    api.get.mockClear()
    const [, nextBtn] = wrapper.findAll('.flex.items-center.justify-between > button')
    await nextBtn.trigger('click')
    await flushAll()
    expect(api.get).toHaveBeenCalledWith('/calendar', expect.anything())
  })
})
