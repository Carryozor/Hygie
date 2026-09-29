// src/views/__tests__/LogsView.cov.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mountView, flushAll } from './testUtils.cov.js'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
}))

import api from '@/api/client'
import LogsView from '../LogsView.vue'

// Factory, not a shared constant: LogsView mutates `log.seen_status` on the
// exact objects load() hands it (`logs.value = data.logs`, no copy) — reusing
// one array across tests would leak markSeen/ackAll mutations between them.
function makeLogs() {
  return [
    { id: 1, ts: '2026-01-01T10:00:00Z', level: 'ERROR', message: 'Deletion failed', seen_status: null },
    { id: 2, ts: '2026-01-01T09:00:00Z', level: 'INFO', message: 'Scan completed', seen_status: null },
  ]
}

function mockGets(logs) {
  api.get.mockImplementation(url => {
    if (url === '/logs') return Promise.resolve({ data: { logs } })
    return Promise.resolve({ data: {} })
  })
}

describe('LogsView', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows "Aucun log." when there are no entries', async () => {
    mockGets([])
    const { wrapper } = await mountView(LogsView, { path: '/logs' })
    expect(wrapper.text()).toContain('Aucun log.')
  })

  it('shows the error message when /logs fails to load', async () => {
    api.get.mockRejectedValue({ response: { status: 500 } })
    const { wrapper } = await mountView(LogsView, { path: '/logs' })
    expect(wrapper.text()).toContain('Impossible de charger les journaux.')
  })

  it('renders log entries and only shows mass-action buttons when there is an unseen error', async () => {
    mockGets(makeLogs())
    const { wrapper } = await mountView(LogsView, { path: '/logs' })
    expect(wrapper.text()).toContain('Deletion failed')
    expect(wrapper.text()).toContain('Scan completed')
    expect(wrapper.find('button[title="Tout marquer comme vu"]').exists()).toBe(true)
    expect(wrapper.find('button[title="Accuser réception"]').exists()).toBe(true)
  })

  it('hides the mass-action buttons once there is no unseen error left', async () => {
    mockGets(makeLogs().map(l => ({ ...l, seen_status: l.level === 'ERROR' ? 'seen' : l.seen_status })))
    const { wrapper } = await mountView(LogsView, { path: '/logs' })
    expect(wrapper.find('button[title="Tout marquer comme vu"]').exists()).toBe(false)
  })

  it('clicking a level filter reloads /logs with the level param', async () => {
    mockGets(makeLogs())
    const { wrapper } = await mountView(LogsView, { path: '/logs' })
    api.get.mockClear()
    const errBtn = [...wrapper.findAll('button')].find(b => b.text() === 'Erreur')
    await errBtn.trigger('click')
    await flushAll()
    const call = api.get.mock.calls.find(c => c[0] === '/logs')
    expect(call[1].params.level).toBe('ERROR')
  })

  it('the refresh button reloads /logs', async () => {
    mockGets(makeLogs())
    const { wrapper } = await mountView(LogsView, { path: '/logs' })
    api.get.mockClear()
    await wrapper.find('button[title="Rafraîchir"]').trigger('click')
    await flushAll()
    expect(api.get).toHaveBeenCalledWith('/logs', expect.anything())
  })

  it('markSeen PATCHes the clicked log and refreshes the unseen-errors badge', async () => {
    mockGets(makeLogs())
    api.patch.mockResolvedValue({ data: {} })
    api.get.mockImplementation(url => {
      if (url === '/logs') return Promise.resolve({ data: { logs: makeLogs() } })
      if (url === '/logs/unseen-errors-count') return Promise.resolve({ data: { count: 0 } })
      return Promise.resolve({ data: {} })
    })
    const { wrapper } = await mountView(LogsView, { path: '/logs' })
    const row = wrapper.findAll('.group').find(r => r.text().includes('Deletion failed'))
    await row.find('button[title="Marquer comme vu"]').trigger('click')
    await flushAll()
    expect(api.patch).toHaveBeenCalledWith('/logs/1', { seen_status: 'seen' })
    expect(api.get).toHaveBeenCalledWith('/logs/unseen-errors-count')
  })

  it('markSeenAll POSTs /logs/mark-seen-errors and flips every unseen ERROR row to seen', async () => {
    mockGets(makeLogs())
    api.post.mockResolvedValue({ data: {} })
    const { wrapper } = await mountView(LogsView, { path: '/logs' })
    await wrapper.find('button[title="Tout marquer comme vu"]').trigger('click')
    await flushAll()
    expect(api.post).toHaveBeenCalledWith('/logs/mark-seen-errors')
    // Mass-action buttons disappear once no unseen error remains
    expect(wrapper.find('button[title="Tout marquer comme vu"]').exists()).toBe(false)
  })

  it('ackAll POSTs /logs/ack-errors and flips unseen ERROR rows to acked', async () => {
    mockGets(makeLogs())
    api.post.mockResolvedValue({ data: {} })
    const { wrapper } = await mountView(LogsView, { path: '/logs' })
    await wrapper.find('button[title="Accuser réception"]').trigger('click')
    await flushAll()
    expect(api.post).toHaveBeenCalledWith('/logs/ack-errors')
    expect(wrapper.find('button[title="Tout marquer comme vu"]').exists()).toBe(false)
  })

  it('clearStatus resets a previously-marked log back to unmarked', async () => {
    mockGets(makeLogs().map(l => (l.id === 1 ? { ...l, seen_status: 'seen' } : l)))
    api.patch.mockResolvedValue({ data: {} })
    const { wrapper } = await mountView(LogsView, { path: '/logs' })
    const row = wrapper.findAll('.group').find(r => r.text().includes('Deletion failed'))
    await row.find('button[title="Réinitialiser"]').trigger('click')
    await flushAll()
    expect(api.patch).toHaveBeenCalledWith('/logs/1', { seen_status: null })
  })
})
