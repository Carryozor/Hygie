// src/views/__tests__/DashboardView.cov.test.js
//
// DashboardView is read-only. Coverage focuses on its loading/empty/error
// states and on not leaking an unsafe seerr_request_url into a live link.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mountView, flushAll } from './testUtils.cov.js'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn() },
}))

import api from '@/api/client'
import DashboardView from '../DashboardView.vue'

function mockGets({ media = [], global: g = {}, storage = {} } = {}) {
  api.get.mockImplementation(url => {
    if (url === '/stats/global') return Promise.resolve({ data: g })
    if (url === '/storage') return Promise.resolve({ data: storage })
    if (url === '/media') return Promise.resolve({ data: { items: media } })
    return Promise.resolve({ data: {} })
  })
}

describe('DashboardView', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows the loading state while /media has not resolved', async () => {
    let resolveMedia
    api.get.mockImplementation(url => {
      if (url === '/stats/global') return Promise.resolve({ data: {} })
      if (url === '/storage') return Promise.resolve({ data: {} })
      if (url === '/media') return new Promise(r => { resolveMedia = r })
      return Promise.resolve({ data: {} })
    })
    const { wrapper } = await mountView(DashboardView, { path: '/' })
    expect(wrapper.text()).toContain('Chargement...')
    resolveMedia({ data: { items: [] } })
    await flushAll()
  })

  it('shows the empty state when there is nothing pending', async () => {
    mockGets({ media: [] })
    const { wrapper } = await mountView(DashboardView, { path: '/' })
    expect(wrapper.text()).toContain('Aucune suppression planifiée')
  })

  it('shows the dashboard error message when /media fails', async () => {
    api.get.mockImplementation(url => {
      if (url === '/stats/global') return Promise.resolve({ data: {} })
      if (url === '/storage') return Promise.resolve({ data: {} })
      if (url === '/media') return Promise.reject({ response: { status: 500 } })
      return Promise.resolve({ data: {} })
    })
    const { wrapper } = await mountView(DashboardView, { path: '/' })
    expect(wrapper.text()).toContain('Impossible de charger les données du tableau de bord.')
  })

  it('renders the queue/deleted/ignored/error stat values from /stats/global', async () => {
    mockGets({
      global: { queue: { pending: 12, error: 3 }, total_deleted: 87, total_ignored: 5, total_scans: 40 },
    })
    const { wrapper } = await mountView(DashboardView, { path: '/' })
    expect(wrapper.text()).toContain('12')
    expect(wrapper.text()).toContain('87')
    expect(wrapper.text()).toContain('5')
    expect(wrapper.text()).toContain('3')
    expect(wrapper.text()).toContain('40')
  })

  it('shows the missing count for Radarr only when total_in_library exceeds count on disk', async () => {
    mockGets({ storage: { movies: { total_in_library: 100, count: 90, monitored: 80, unmonitored: 20, size: 123456 } } })
    const { wrapper } = await mountView(DashboardView, { path: '/' })
    expect(wrapper.text()).toContain('Manquants')
    expect(wrapper.text()).toContain('10') // 100 - 90
  })

  it('hides the missing row when the library is complete (nothing missing)', async () => {
    mockGets({ storage: { movies: { total_in_library: 90, count: 90, monitored: 80, unmonitored: 10 } } })
    const { wrapper } = await mountView(DashboardView, { path: '/' })
    expect(wrapper.text()).not.toContain('Manquants')
  })

  it('colors the disk usage bar red above 90% and shows used/total sizes', async () => {
    mockGets({ storage: { disks: [{ path: '/data', source: 'radarr', total: 100, free: 5 }] } })
    const { wrapper } = await mountView(DashboardView, { path: '/' })
    expect(wrapper.text()).toContain('/data')
    const bar = wrapper.find('.h-full.rounded-full.transition-all')
    expect(bar.classes()).toContain('bg-red-400')
    expect(bar.attributes('style')).toContain('95%')
  })

  it('neutralizes a javascript: seerr_request_url on an upcoming-deletion row instead of linking it live', async () => {
    mockGets({
      media: [{ id: 1, title: 'Dune', status: 'pending', delete_at: '2099-01-01T00:00:00Z', seerr_request_url: 'javascript:alert(1)' }],
    })
    const { wrapper } = await mountView(DashboardView, { path: '/' })
    // The "Voir tout" router-link (href="/queue") always renders — the
    // per-row title link is the other one, scoped to the table body.
    const link = wrapper.find('tbody a[href]')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toBe('#')
  })

  it('renders a plain (non-link) title when the item has no seerr_request_url', async () => {
    mockGets({ media: [{ id: 1, title: 'Dune', status: 'pending', delete_at: '2099-01-01T00:00:00Z' }] })
    const { wrapper } = await mountView(DashboardView, { path: '/' })
    expect(wrapper.find('tbody a[href]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Dune')
  })

  it('renders Sonarr episode stats and this-month deleted count', async () => {
    const now = new Date()
    const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
    mockGets({
      global: { total_deleted: 20, by_month: [{ month, deleted: 6 }] },
      storage: { series: { count: 5, episodes: 120, episodes_total: 200, episodes_aired: 150, monitored: 4, unmonitored: 1, size: 999 } },
    })
    const { wrapper } = await mountView(DashboardView, { path: '/' })
    expect(wrapper.text()).toContain('Épisodes sur disque')
    expect(wrapper.text()).toContain('Total épisodes')
    expect(wrapper.text()).toContain('Épisodes diffusés')
    expect(wrapper.text()).toContain('6 ce mois')
  })

  it('hides a broken poster thumbnail on img error', async () => {
    mockGets({ media: [{ id: 1, title: 'Dune', status: 'pending', delete_at: '2099-01-01T00:00:00Z', poster_url: 'https://x/p.jpg' }] })
    const { wrapper } = await mountView(DashboardView, { path: '/' })
    const img = wrapper.find('img')
    expect(img.exists()).toBe(true)
    await img.trigger('error')
    expect(img.element.style.display).toBe('none')
  })

  it('colors the disk usage bar orange between 75% and 90% used', async () => {
    mockGets({ storage: { disks: [{ path: '/data', source: 'radarr', total: 100, free: 20 }] } }) // 80% used
    const { wrapper } = await mountView(DashboardView, { path: '/' })
    const bar = wrapper.find('.h-full.rounded-full.transition-all')
    expect(bar.classes()).toContain('bg-orange-400')
  })

  it('labels an overdue item as "Dépassé" and a same-day one as "Aujourd\'hui"', async () => {
    const past = new Date(Date.now() - 86400000).toISOString()
    const today = new Date().toISOString()
    mockGets({
      media: [
        { id: 1, title: 'Overdue', status: 'pending', delete_at: past },
        { id: 2, title: 'Today', status: 'pending', delete_at: today },
      ],
    })
    const { wrapper } = await mountView(DashboardView, { path: '/' })
    expect(wrapper.text()).toContain('Dépassé')
    expect(wrapper.text()).toContain("Aujourd'hui")
  })
})
