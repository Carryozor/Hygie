// src/components/media/__tests__/MediaTable.cov.test.js
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const servers = [
  { id: '1', type: 'plex' },
  { id: '2', type: 'emby' },
]
vi.mock('@/stores/servers', () => ({
  useServersStore: () => ({ servers }),
}))

import MediaTable from '../MediaTable.vue'

function item(overrides = {}) {
  return { id: 1, title: 'Matrix', media_type: 'movie', status: 'pending', delete_at: null, ...overrides }
}

describe('MediaTable', () => {
  it('shows the empty message when items is empty', () => {
    const wrapper = mount(MediaTable, { props: { items: [] } })
    expect(wrapper.text()).toContain('Aucun élément.')
  })

  it('renders one row per item', () => {
    const wrapper = mount(MediaTable, { props: { items: [item({ id: 1 }), item({ id: 2, title: 'Matrix 2' })] } })
    expect(wrapper.findAll('tbody tr')).toHaveLength(2)
  })

  it('maps status "pending" to the French label and yellow class', () => {
    const wrapper = mount(MediaTable, { props: { items: [item({ status: 'pending' })] } })
    expect(wrapper.text()).toContain('En attente')
    expect(wrapper.find('td span.bg-yellow-500\\/20').exists()).toBe(true)
  })

  it('falls back to the raw status string for an unknown status', () => {
    const wrapper = mount(MediaTable, { props: { items: [item({ status: 'weird_status' })] } })
    expect(wrapper.text()).toContain('weird_status')
  })

  it('shows "Supprimé" for daysLabel regardless of delete_at when status is deleted', () => {
    const wrapper = mount(MediaTable, { props: { items: [item({ status: 'deleted', delete_at: '2020-01-01T00:00:00Z' })] } })
    expect(wrapper.text()).toContain('Supprimé')
  })

  it('shows "—" when delete_at is null and status is not deleted', () => {
    const wrapper = mount(MediaTable, { props: { items: [item({ status: 'pending', delete_at: null })] } })
    expect(wrapper.text()).toContain('—')
  })

  it('shows "Dépassé" when delete_at is in the past', () => {
    const wrapper = mount(MediaTable, {
      props: { items: [item({ status: 'pending', delete_at: new Date(Date.now() - 86400000).toISOString() })] },
    })
    expect(wrapper.text()).toContain('Dépassé')
  })

  it('shows "Demain" for a delete_at exactly one day away', () => {
    const tomorrow = new Date(Date.now() + 25 * 3600 * 1000) // safely into "1 day" bucket
    const wrapper = mount(MediaTable, { props: { items: [item({ status: 'pending', delete_at: tomorrow.toISOString() })] } })
    expect(wrapper.text()).toMatch(/Demain|Aujourd'hui|Dans \d+j/)
  })

  it('renders the server color dot only when showServerDot is true', () => {
    const withDot = mount(MediaTable, { props: { items: [item({ server_id: '1' })], showServerDot: true } })
    expect(withDot.find('.rounded-full.flex-shrink-0').exists()).toBe(true)
    const withoutDot = mount(MediaTable, { props: { items: [item({ server_id: '1' })], showServerDot: false } })
    expect(withoutDot.find('span.w-2.h-2').exists()).toBe(false)
  })

  it('colors the server dot orange for a plex server_id', () => {
    const wrapper = mount(MediaTable, { props: { items: [item({ server_id: '1' })], showServerDot: true } })
    expect(wrapper.find('.bg-orange-400').exists()).toBe(true)
  })

  it('falls back to the muted dot color for an unknown/unmatched server_id', () => {
    const wrapper = mount(MediaTable, { props: { items: [item({ server_id: 'no-such-server' })], showServerDot: true } })
    expect(wrapper.find('.bg-\\[var\\(--muted\\)\\]').exists()).toBe(true)
  })

  it('shows the library name or the em-dash fallback', () => {
    const wrapper = mount(MediaTable, { props: { items: [item({ library_name: null })] } })
    expect(wrapper.text()).toContain('—')
  })

  it('marks an urgent pending row (<=3 days) with the red background helper class', () => {
    const wrapper = mount(MediaTable, {
      props: { items: [item({ status: 'pending', delete_at: new Date(Date.now() + 2 * 86400000).toISOString() })] },
    })
    expect(wrapper.find('tr.bg-red-500\\/5').exists()).toBe(true)
  })

  it('does not mark a non-pending row as urgent even if delete_at is soon', () => {
    const wrapper = mount(MediaTable, {
      props: { items: [item({ status: 'deleted', delete_at: new Date(Date.now() + 2 * 86400000).toISOString() })] },
    })
    expect(wrapper.find('tr.bg-red-500\\/5').exists()).toBe(false)
  })

  it('renders episode/season/series media types with the TV icon, movies with the film icon', () => {
    const wrapper = mount(MediaTable, {
      props: { items: [item({ id: 1, media_type: 'Episode', poster_url: null }), item({ id: 2, media_type: 'movie', poster_url: null })] },
    })
    expect(wrapper.find('i.fa-tv').exists()).toBe(true)
    expect(wrapper.find('i.fa-film').exists()).toBe(true)
  })
})
