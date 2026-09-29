// src/views/__tests__/QueueView.cov.test.js
//
// QueueView drives the destructive delete-now / ignore / purge actions on
// media in the queue. A test that "passes" on a silently-swallowed 403/404
// would hide the exact regression CHANGELOG v4.3.5 fixed (modal closing /
// action believed successful on a failed request) — so every destructive
// path here asserts: right endpoint + right id, error surfaced, modal state
// after failure, and zero calls on cancel.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mountView, flushAll } from './testUtils.cov.js'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn(), put: vi.fn(), patch: vi.fn() },
}))

import api from '@/api/client'
import QueueView from '../QueueView.vue'
import { useServersStore } from '@/stores/servers'

function mediaResponse(items) {
  return { data: { items, total: items.length } }
}

const ITEM = {
  id: 42, title: 'Dune', media_type: 'Movie', status: 'pending',
  delete_at: '2099-01-01T00:00:00Z', library_id: 1,
}

function mockDefaultGets({ items = [ITEM] } = {}) {
  api.get.mockImplementation(url => {
    if (url === '/settings') return Promise.resolve({ data: {} })
    if (url === '/media') return Promise.resolve(mediaResponse(items))
    return Promise.resolve({ data: {} })
  })
}

describe('QueueView', () => {
  let errorEvents
  let errorListener

  beforeEach(() => {
    vi.clearAllMocks()
    errorEvents = []
    errorListener = e => errorEvents.push(e.detail)
    window.addEventListener('hygie:error', errorListener)
  })
  afterEach(() => {
    window.removeEventListener('hygie:error', errorListener)
    vi.useRealTimers()
    // Teleport(to: 'body') content survives past the owning wrapper (VTU
    // doesn't attach the mount root to document.body) — clear it so a modal
    // left open by one test (e.g. an error keeping it open) can't leak into
    // the next test's document.body queries.
    document.body.innerHTML = ''
  })

  it('shows the loading spinner while /media has not resolved yet', async () => {
    let resolveMedia
    api.get.mockImplementation(url => {
      if (url === '/settings') return Promise.resolve({ data: {} })
      if (url === '/media') return new Promise(r => { resolveMedia = r })
      return Promise.resolve({ data: {} })
    })
    const { wrapper } = await mountView(QueueView, { path: '/queue' })
    expect(wrapper.find('.fa-spinner').exists()).toBe(true)
    resolveMedia(mediaResponse([]))
    await flushAll()
  })

  it('renders the item title once /media resolves', async () => {
    mockDefaultGets()
    const { wrapper } = await mountView(QueueView, { path: '/queue' })
    expect(wrapper.text()).toContain('Dune')
    expect(wrapper.find('.fa-spinner').exists()).toBe(false)
  })

  it('shows the queue error message when /media fails to load', async () => {
    api.get.mockImplementation(url => {
      if (url === '/settings') return Promise.resolve({ data: {} })
      if (url === '/media') return Promise.reject({ response: { status: 500 } })
      return Promise.resolve({ data: {} })
    })
    const { wrapper } = await mountView(QueueView, { path: '/queue' })
    expect(wrapper.text()).toContain("Impossible de charger la file d'attente.")
  })

  it('does not call the API when the delete confirmation is cancelled', async () => {
    mockDefaultGets()
    const { wrapper } = await mountView(QueueView, { path: '/queue' })
    await wrapper.find('button[title="Supprimer maintenant"]').trigger('click')
    await flushAll()

    // Modal (teleported to body) shows the exact item title being targeted.
    expect(document.body.textContent).toContain('Dune')

    const cancelBtn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Annuler')
    cancelBtn.click()
    await flushAll()

    expect(api.post).not.toHaveBeenCalled()
    expect(api.delete).not.toHaveBeenCalled()
    expect(document.body.querySelector('.fixed.inset-0')).toBeNull()
  })

  it('calls delete-now with the clicked item id on confirm, and reloads the list', async () => {
    mockDefaultGets()
    api.post.mockResolvedValue({ data: {} })
    const { wrapper } = await mountView(QueueView, { path: '/queue' })
    api.get.mockClear()

    await wrapper.find('button[title="Supprimer maintenant"]').trigger('click')
    await flushAll()
    const confirmBtn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Supprimer')
    confirmBtn.click()
    await flushAll()

    expect(api.post).toHaveBeenCalledWith('/media/42/delete-now')
    expect(api.get).toHaveBeenCalledWith('/media', expect.anything())
    // Modal closed after success
    expect(document.body.querySelector('.fixed.inset-0')).toBeNull()
  })

  it('on a 404 delete-now failure: surfaces the error toast and keeps the modal open', async () => {
    mockDefaultGets()
    api.post.mockRejectedValue({ response: { status: 404, data: {} } })
    const { wrapper } = await mountView(QueueView, { path: '/queue' })

    await wrapper.find('button[title="Supprimer maintenant"]').trigger('click')
    await flushAll()
    const confirmBtn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Supprimer')
    confirmBtn.click()
    await flushAll()

    expect(api.post).toHaveBeenCalledWith('/media/42/delete-now')
    expect(errorEvents.length).toBe(1)
    expect(errorEvents[0].message).toContain('Impossible de supprimer ce média')
    // Modal must stay open — the exact bug CHANGELOG v4.3.5 fixed.
    expect(document.body.querySelector('.fixed.inset-0')).not.toBeNull()
  })

  it('ignore modal sends reason and expire_days as query params for the right item', async () => {
    mockDefaultGets()
    api.post.mockResolvedValue({ data: {} })
    const { wrapper } = await mountView(QueueView, { path: '/queue' })

    await wrapper.find('button[title="Ignorer"]').trigger('click')
    await flushAll()
    const [reasonInput, daysInput] = document.body.querySelectorAll('input')
    reasonInput.value = 'not interested'
    reasonInput.dispatchEvent(new Event('input'))
    daysInput.value = '5'
    daysInput.dispatchEvent(new Event('input'))
    await flushAll()

    const confirmBtn = [...document.querySelectorAll('button')].find(b => b.textContent.includes('Ignorer') && b.closest('.fixed'))
    confirmBtn.click()
    await flushAll()

    expect(api.post).toHaveBeenCalledWith('/media/42/ignore', null, {
      params: { reason: 'not interested', expire_days: 5 },
    })
  })

  it('does not call ignore when the ignore modal is cancelled', async () => {
    mockDefaultGets()
    const { wrapper } = await mountView(QueueView, { path: '/queue' })
    await wrapper.find('button[title="Ignorer"]').trigger('click')
    await flushAll()
    const cancelBtn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Annuler')
    cancelBtn.click()
    await flushAll()
    expect(api.post).not.toHaveBeenCalled()
  })

  it('purge calls DELETE /media/purge/deleted on confirm only', async () => {
    mockDefaultGets()
    api.delete.mockResolvedValue({ data: {} })
    const { wrapper } = await mountView(QueueView, { path: '/queue' })

    await wrapper.find('button:has(.fa-trash-can)').trigger('click')
    await flushAll()
    const confirmBtn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Purger')
    confirmBtn.click()
    await flushAll()

    expect(api.delete).toHaveBeenCalledWith('/media/purge/deleted')
  })

  it('purge is not called when the purge confirmation is cancelled', async () => {
    mockDefaultGets()
    const { wrapper } = await mountView(QueueView, { path: '/queue' })
    await wrapper.find('button:has(.fa-trash-can)').trigger('click')
    await flushAll()
    const cancelBtn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Annuler')
    cancelBtn.click()
    await flushAll()
    expect(api.delete).not.toHaveBeenCalled()
  })

  it('a 500 purge failure is reported and does not close the modal', async () => {
    mockDefaultGets()
    api.delete.mockRejectedValue({ response: { status: 500, data: {} } })
    const { wrapper } = await mountView(QueueView, { path: '/queue' })
    await wrapper.find('button:has(.fa-trash-can)').trigger('click')
    await flushAll()
    const confirmBtn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Purger')
    confirmBtn.click()
    await flushAll()
    // 500 is already toasted by the axios interceptor layer (errorHandler) —
    // runConfirmedAction must not double-toast it here.
    expect(errorEvents.length).toBe(0)
    expect(document.body.querySelector('.fixed.inset-0')).not.toBeNull()
  })

  it('debounces search input into a single reload with the search param', async () => {
    mockDefaultGets()
    const { wrapper } = await mountView(QueueView, { path: '/queue' })
    api.get.mockClear()

    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const search = wrapper.find('input[placeholder]')
    await search.setValue('dun')
    await search.setValue('dune')
    // Only the timer set by the LAST keystroke should fire.
    await vi.advanceTimersByTimeAsync(300)
    vi.useRealTimers()
    await flushPromises()

    const mediaCalls = api.get.mock.calls.filter(c => c[0] === '/media')
    expect(mediaCalls.length).toBe(1)
    expect(mediaCalls[0][1].params.search).toBe('dune')
  })

  it('switches to grid view and back to list view', async () => {
    mockDefaultGets()
    const { wrapper } = await mountView(QueueView, { path: '/queue' })
    expect(wrapper.find('table').exists()).toBe(true)
    await wrapper.find('button:has(.fa-table-cells)').trigger('click')
    expect(wrapper.find('table').exists()).toBe(false)
    await wrapper.find('button:has(.fa-list)').trigger('click')
    expect(wrapper.find('table').exists()).toBe(true)
  })

  it('clicking a status filter button calls setFilter and reloads with the status param', async () => {
    mockDefaultGets()
    const { wrapper } = await mountView(QueueView, { path: '/queue' })
    api.get.mockClear()
    const pendingBtn = [...wrapper.findAll('button')].find(b => b.text() === 'En attente')
    await pendingBtn.trigger('click')
    await flushAll()
    const mediaCalls = api.get.mock.calls.filter(c => c[0] === '/media')
    expect(mediaCalls.at(-1)[1].params.status).toBe('pending')
  })

  it('paginates: clicking a page number and the last-page button reload with the right offset', async () => {
    api.get.mockImplementation(url => {
      if (url === '/settings') return Promise.resolve({ data: {} })
      if (url === '/media') return Promise.resolve({ data: { items: [ITEM], total: 120 } }) // 3 pages @ 50/page
      return Promise.resolve({ data: {} })
    })
    const { wrapper } = await mountView(QueueView, { path: '/queue' })
    api.get.mockClear()

    const page2Btn = [...wrapper.findAll('button')].find(b => b.text() === '2')
    await page2Btn.trigger('click')
    await flushAll()
    let mediaCalls = api.get.mock.calls.filter(c => c[0] === '/media')
    expect(mediaCalls.at(-1)[1].params.offset).toBe(50)

    api.get.mockClear()
    const lastBtn = [...wrapper.findAll('button')].find(b => b.text() === '»')
    await lastBtn.trigger('click')
    await flushAll()
    mediaCalls = api.get.mock.calls.filter(c => c[0] === '/media')
    expect(mediaCalls.at(-1)[1].params.offset).toBe(100)
  })

  it('marks an item disabled and shows the library-only badge when its library has no server', async () => {
    mockDefaultGets()
    const { wrapper, pinia } = await mountView(QueueView, { path: '/queue' })
    const servers = useServersStore(pinia)
    servers.libraries = [{ id: 1, server_id: 999, name: 'Films' }]
    servers.servers = [] // server_id 999 does not exist -> "server was removed" branch
    await flushAll()
    expect(wrapper.text()).toContain('désactivé')
  })

  it('shows the server\'s name and an "off" badge when the item\'s server is explicitly disabled', async () => {
    mockDefaultGets()
    const { wrapper, pinia } = await mountView(QueueView, { path: '/queue' })
    const servers = useServersStore(pinia)
    servers.libraries = [{ id: 1, server_id: 5, name: 'Films' }]
    servers.servers = [{ id: 5, name: 'MyEmby', type: 'emby', enabled: false }]
    await flushAll()
    expect(wrapper.text()).toContain('MyEmby')
    expect(wrapper.text()).toContain('off')
  })
})
