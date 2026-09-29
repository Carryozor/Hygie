// src/views/__tests__/LibraryView.cov.test.js
//
// LibraryView is read-only (no destructive actions) — coverage here targets
// its loading/error/not-found/found states and the pending/deleted counts
// derived from the fetched media list.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mountView, flushAll } from './testUtils.cov.js'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn() },
}))

import api from '@/api/client'
import LibraryView from '../LibraryView.vue'

const LIBRARY = { id: 5, name: 'Films 4K', deletion_unit: 'movie' }

describe('LibraryView', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows "Bibliothèque introuvable." when the library id does not match any known library', async () => {
    api.get.mockImplementation(url => {
      if (url === '/settings/media-servers') return Promise.resolve({ data: [] })
      if (url === '/libraries') return Promise.resolve({ data: [] }) // nothing matches id 999
      if (url === '/media') return Promise.resolve({ data: { items: [] } })
      return Promise.resolve({ data: {} })
    })
    const { wrapper } = await mountView(LibraryView, { path: '/library/999' })
    expect(wrapper.text()).toContain('Bibliothèque introuvable.')
  })

  it('shows the error message when /media fails to load', async () => {
    api.get.mockImplementation(url => {
      if (url === '/settings/media-servers') return Promise.resolve({ data: [] })
      if (url === '/libraries') return Promise.resolve({ data: [LIBRARY] })
      if (url === '/media') return Promise.reject({ response: { status: 500 } })
      return Promise.resolve({ data: {} })
    })
    const { wrapper } = await mountView(LibraryView, { path: '/library/5' })
    expect(wrapper.text()).toContain('Impossible de charger la bibliothèque.')
  })

  it('renders the library name and pending/deleted counts once loaded', async () => {
    api.get.mockImplementation(url => {
      if (url === '/settings/media-servers') return Promise.resolve({ data: [] })
      if (url === '/libraries') return Promise.resolve({ data: [LIBRARY] })
      if (url === '/media') return Promise.resolve({
        data: {
          items: [
            { id: 1, title: 'A', status: 'pending' },
            { id: 2, title: 'B', status: 'pending' },
            { id: 3, title: 'C', status: 'deleted' },
          ],
        },
      })
      return Promise.resolve({ data: {} })
    })
    const { wrapper } = await mountView(LibraryView, { path: '/library/5' })
    expect(wrapper.text()).toContain('Films 4K')
    expect(wrapper.text()).toContain('2') // pending count
    expect(wrapper.text()).toContain('1') // deleted count
  })

  it('re-fetches when the route id param changes', async () => {
    api.get.mockImplementation(url => {
      if (url === '/settings/media-servers') return Promise.resolve({ data: [] })
      if (url === '/libraries') return Promise.resolve({ data: [LIBRARY, { id: 6, name: 'Séries', deletion_unit: 'episode' }] })
      if (url === '/media') return Promise.resolve({ data: { items: [] } })
      return Promise.resolve({ data: {} })
    })
    const { wrapper, router } = await mountView(LibraryView, { path: '/library/5' })
    expect(wrapper.text()).toContain('Films 4K')
    api.get.mockClear()
    await router.push('/library/6')
    await flushAll()
    const mediaCalls = api.get.mock.calls.filter(c => c[0] === '/media')
    expect(mediaCalls.at(-1)[1].params.library_id).toBe('6')
    expect(wrapper.text()).toContain('Séries')
  })
})
