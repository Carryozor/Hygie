// src/views/__tests__/IgnoredView.cov.test.js
//
// IgnoredView: requeue (undo ignore) and remove (permanently drop from the
// ignore list) are the two mutating actions. Remove has an explicit confirm
// modal; requeue does not (single click) but must still hit the right id and
// surface failures instead of silently no-op'ing.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { DOMWrapper } from '@vue/test-utils'
import { mountView, flushAll } from './testUtils.cov.js'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}))

import api from '@/api/client'
import IgnoredView from '../IgnoredView.vue'

const ITEM = { id: 7, title: 'The Wire', media_type: 'Series', reason: 'rewatch later', ignored_at: '2026-01-01T00:00:00Z' }

describe('IgnoredView', () => {
  let errorEvents, errorListener

  beforeEach(() => {
    vi.clearAllMocks()
    errorEvents = []
    errorListener = e => errorEvents.push(e.detail)
    window.addEventListener('hygie:error', errorListener)
  })
  afterEach(() => {
    window.removeEventListener('hygie:error', errorListener)
    // Teleport(to: 'body') content is appended straight to document.body,
    // outside the (detached) mount root VTU tears down automatically — left
    // uncleaned, a modal from one test (e.g. the "stays open on error" case)
    // leaks into document.body queries in the next test.
    document.body.innerHTML = ''
  })

  it('shows the empty state when /ignored returns no items', async () => {
    api.get.mockResolvedValue({ data: [] })
    const { wrapper } = await mountView(IgnoredView, { path: '/ignored' })
    expect(wrapper.text()).toContain('Aucun média ignoré.')
  })

  it('shows the loadFailed error message when /ignored rejects', async () => {
    api.get.mockRejectedValue({ response: { status: 500 } })
    const { wrapper } = await mountView(IgnoredView, { path: '/ignored' })
    expect(wrapper.text()).toContain('Impossible de charger les médias ignorés.')
  })

  it('renders ignored items once loaded', async () => {
    api.get.mockResolvedValue({ data: [ITEM] })
    const { wrapper } = await mountView(IgnoredView, { path: '/ignored' })
    expect(wrapper.text()).toContain('The Wire')
    expect(wrapper.text()).toContain('rewatch later')
  })

  it('requeue posts to /ignored/{id}/requeue for the clicked item and reloads', async () => {
    api.get.mockResolvedValue({ data: [ITEM] })
    api.post.mockResolvedValue({ data: {} })
    const { wrapper } = await mountView(IgnoredView, { path: '/ignored' })
    api.get.mockClear()

    await wrapper.find('button[title="Remettre en file"]').trigger('click')
    await flushAll()

    expect(api.post).toHaveBeenCalledWith('/ignored/7/requeue')
    expect(api.get).toHaveBeenCalledTimes(1) // reload
  })

  it('surfaces an error toast when requeue fails (e.g. item already gone)', async () => {
    api.get.mockResolvedValue({ data: [ITEM] })
    api.post.mockRejectedValue({ response: { status: 404, data: {} } })
    const { wrapper } = await mountView(IgnoredView, { path: '/ignored' })

    await wrapper.find('button[title="Remettre en file"]').trigger('click')
    await flushAll()

    expect(errorEvents.length).toBe(1)
    expect(errorEvents[0].message).toContain('Impossible de remettre ce média en file')
  })

  it('does not call the API when remove is cancelled', async () => {
    api.get.mockResolvedValue({ data: [ITEM] })
    const { wrapper } = await mountView(IgnoredView, { path: '/ignored' })

    await wrapper.find('button[title="Supprimer définitivement"]').trigger('click')
    await flushAll()
    expect(document.body.textContent).toContain('The Wire')

    const cancelBtn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Annuler')
    cancelBtn.click()
    await flushAll()

    expect(api.delete).not.toHaveBeenCalled()
    expect(document.body.querySelector('.fixed.inset-0')).toBeNull()
  })

  it('remove deletes /ignored/{id} for the confirmed item and closes the modal', async () => {
    api.get.mockResolvedValue({ data: [ITEM] })
    api.delete.mockResolvedValue({ data: {} })
    const { wrapper } = await mountView(IgnoredView, { path: '/ignored' })

    await wrapper.find('button[title="Supprimer définitivement"]').trigger('click')
    await flushAll()
    const removeBtn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Retirer')
    removeBtn.click()
    await flushAll()

    expect(api.delete).toHaveBeenCalledWith('/ignored/7')
    expect(document.body.querySelector('.fixed.inset-0')).toBeNull()
  })

  it('a failed remove keeps the confirmation target and surfaces the error', async () => {
    api.get.mockResolvedValue({ data: [ITEM] })
    api.delete.mockRejectedValue({ response: { status: 403, data: {} } })
    const { wrapper } = await mountView(IgnoredView, { path: '/ignored' })

    await wrapper.find('button[title="Supprimer définitivement"]').trigger('click')
    await flushAll()
    const removeBtn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Retirer')
    removeBtn.click()
    await flushAll()

    expect(errorEvents.length).toBe(1)
    expect(errorEvents[0].message).toContain('Impossible de retirer ce média')
    expect(document.body.querySelector('.fixed.inset-0')).not.toBeNull()
  })

  it('hides a broken poster image on load error instead of showing a broken-image icon', async () => {
    const withPoster = { ...ITEM, poster_url: 'https://example.com/poster.jpg' }
    api.get.mockResolvedValue({ data: [withPoster] })
    const { wrapper } = await mountView(IgnoredView, { path: '/ignored' })
    const img = wrapper.find('img')
    expect(img.exists()).toBe(true)
    await img.trigger('error')
    expect(img.element.style.display).toBe('none')
  })

  it('shows "Permanent" for an item with no expire_at, and the expiry date (orange if soon) otherwise', async () => {
    const soon = new Date(Date.now() + 2 * 86400000).toISOString() // expires in 2 days
    const later = new Date(Date.now() + 30 * 86400000).toISOString()
    api.get.mockResolvedValue({
      data: [
        { ...ITEM, id: 1, title: 'Permanent one', expire_at: null },
        { ...ITEM, id: 2, title: 'Expiring soon', expire_at: soon },
        { ...ITEM, id: 3, title: 'Expiring later', expire_at: later },
      ],
    })
    const { wrapper } = await mountView(IgnoredView, { path: '/ignored' })
    expect(wrapper.text()).toContain('Permanent')
    const soonCell = wrapper.findAll('span').find(s => s.classes().includes('text-orange-400'))
    expect(soonCell).toBeTruthy()
  })

  it('formats a missing ignored_at as an empty cell rather than "Invalid Date"', async () => {
    api.get.mockResolvedValue({ data: [{ ...ITEM, ignored_at: null }] })
    const { wrapper } = await mountView(IgnoredView, { path: '/ignored' })
    expect(wrapper.text()).not.toContain('Invalid Date')
  })

  it('clicking the modal backdrop (outside the dialog) cancels the remove confirmation', async () => {
    api.get.mockResolvedValue({ data: [ITEM] })
    const { wrapper } = await mountView(IgnoredView, { path: '/ignored' })
    await wrapper.find('button[title="Supprimer définitivement"]').trigger('click')
    await flushAll()
    const backdrop = document.body.querySelector('.fixed.inset-0')
    expect(backdrop).not.toBeNull()
    await new DOMWrapper(backdrop).trigger('mousedown')
    await flushAll()
    expect(document.body.querySelector('.fixed.inset-0')).toBeNull()
    expect(api.delete).not.toHaveBeenCalled()
  })

  it('debounces the search box into a single /ignored call with the search param', async () => {
    api.get.mockResolvedValue({ data: [] })
    const { wrapper } = await mountView(IgnoredView, { path: '/ignored' })
    api.get.mockClear()

    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    await wrapper.find('input[placeholder]').setValue('wir')
    await wrapper.find('input[placeholder]').setValue('wire')
    await vi.advanceTimersByTimeAsync(300)
    vi.useRealTimers()
    await flushAll()

    expect(api.get).toHaveBeenCalledTimes(1)
    expect(api.get).toHaveBeenCalledWith('/ignored', { params: { search: 'wire' } })
  })
})
