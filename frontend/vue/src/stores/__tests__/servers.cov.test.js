// src/stores/__tests__/servers.cov.test.js
// Coverage for stores/servers.js (0% before this file): fetch() must land
// servers+libraries in state on success and set a discoverable error without
// touching state on failure; librariesForServer must filter by server id
// (including the implicit '0' default) as a string comparison.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn() },
}))

import api from '@/api/client'
import { useServersStore } from '../servers'

describe('useServersStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('initial state is empty', () => {
    const s = useServersStore()
    expect(s.servers).toEqual([])
    expect(s.libraries).toEqual([])
    expect(s.error).toBeNull()
  })

  it('fetch populates servers and libraries from the two endpoints', async () => {
    api.get.mockImplementation(url => {
      if (url === '/settings/media-servers') return Promise.resolve({ data: [{ id: '1', enabled: true }] })
      if (url === '/libraries') return Promise.resolve({ data: [{ id: 'lib1', server_id: '1' }] })
      throw new Error(`unexpected url ${url}`)
    })
    const s = useServersStore()
    await s.fetch()
    expect(s.servers).toEqual([{ id: '1', enabled: true }])
    expect(s.libraries).toEqual([{ id: 'lib1', server_id: '1' }])
    expect(s.error).toBeNull()
  })

  it('fetch defaults servers/libraries to [] when the API returns no data field', async () => {
    api.get.mockResolvedValue({ data: undefined })
    const s = useServersStore()
    await s.fetch()
    expect(s.servers).toEqual([])
    expect(s.libraries).toEqual([])
  })

  it('fetch sets error message and does not update state on API failure', async () => {
    api.get.mockRejectedValue(new Error('network down'))
    const s = useServersStore()
    await s.fetch()
    expect(s.error).toBe('network down')
    expect(s.servers).toEqual([])
    expect(s.libraries).toEqual([])
  })

  it('fetch falls back to a generic error message when the rejection has no .message', async () => {
    api.get.mockRejectedValue({})
    const s = useServersStore()
    await s.fetch()
    expect(s.error).toBe('fetch error')
  })

  it('librariesForServer filters libraries by exact server id (string comparison)', () => {
    const s = useServersStore()
    s.libraries = [
      { id: 'a', server_id: '1' },
      { id: 'b', server_id: '2' },
      { id: 'c', server_id: '1' },
    ]
    expect(s.librariesForServer('1')).toEqual([
      { id: 'a', server_id: '1' },
      { id: 'c', server_id: '1' },
    ])
  })

  it('librariesForServer treats a numeric id and matching string id as equal', () => {
    const s = useServersStore()
    s.libraries = [{ id: 'a', server_id: '1' }]
    expect(s.librariesForServer(1)).toEqual([{ id: 'a', server_id: '1' }])
  })

  it('librariesForServer defaults a missing server_id on a library to "0"', () => {
    const s = useServersStore()
    s.libraries = [{ id: 'a', server_id: undefined }]
    expect(s.librariesForServer('0')).toEqual([{ id: 'a', server_id: undefined }])
  })

  it('librariesForServer returns [] when nothing matches', () => {
    const s = useServersStore()
    s.libraries = [{ id: 'a', server_id: '1' }]
    expect(s.librariesForServer('99')).toEqual([])
  })
})
