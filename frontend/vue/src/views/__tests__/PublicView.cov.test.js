// src/views/__tests__/PublicView.cov.test.js
//
// PublicView is the unauthenticated route — its one security requirement is
// that it never leaks an internal/unsafe URL or the admin token into the
// rendered DOM. It talks to the backend via global fetch (not api/client.js),
// so we mock window.fetch directly.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mountView, flushAll } from './testUtils.cov.js'

vi.mock('@/api/tokenStore', () => ({
  getToken: vi.fn(() => ''),
  setToken: vi.fn(),
  clearToken: vi.fn(),
}))

import { getToken } from '@/api/tokenStore'
import PublicView from '../PublicView.vue'

function jsonResponse(status, body) {
  return Promise.resolve({ status, ok: status >= 200 && status < 300, json: () => Promise.resolve(body) })
}

describe('PublicView', () => {
  let fetchMock
  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    sessionStorage.clear()
    localStorage.clear()
    getToken.mockReturnValue('')
  })
  afterEach(() => vi.unstubAllGlobals())

  it('shows the disabled message on a 404 (dashboard not configured)', async () => {
    fetchMock.mockReturnValue(jsonResponse(404, {}))
    const { wrapper } = await mountView(PublicView, { path: '/myslug' })
    expect(wrapper.text()).toContain("Le tableau de bord public n'est pas activé.")
  })

  it('shows the disabled message when the backend reports error: "disabled"', async () => {
    fetchMock.mockReturnValue(jsonResponse(403, { error: 'disabled' }))
    const { wrapper } = await mountView(PublicView, { path: '/myslug' })
    expect(wrapper.text()).toContain("Le tableau de bord public n'est pas activé.")
  })

  it('shows the password form when password_required, without leaking any data', async () => {
    fetchMock.mockReturnValue(jsonResponse(401, { error: 'password_required' }))
    const { wrapper } = await mountView(PublicView, { path: '/myslug' })
    expect(wrapper.text()).toContain('Accès protégé')
    expect(wrapper.find('input[type="password"]').exists()).toBe(true)
  })

  it('shows "wrong password" only after a failed password submission, not on first load', async () => {
    fetchMock.mockReturnValue(jsonResponse(401, { error: 'password_required' }))
    const { wrapper } = await mountView(PublicView, { path: '/myslug' })
    expect(wrapper.text()).not.toContain('Mot de passe incorrect.')

    fetchMock.mockReturnValue(jsonResponse(403, { error: 'wrong_password' }))
    await wrapper.find('input[type="password"]').setValue('nope')
    await submitBtn(wrapper).trigger('click')
    await flushAll()
    expect(wrapper.text()).toContain('Mot de passe incorrect.')
  })

  it('sends the password in a header, never in the query string', async () => {
    fetchMock.mockReturnValue(jsonResponse(401, { error: 'password_required' }))
    const { wrapper } = await mountView(PublicView, { path: '/myslug' })
    fetchMock.mockClear()
    fetchMock.mockReturnValue(jsonResponse(200, { events: {}, libraries: [], servers: [] }))

    await wrapper.find('input[type="password"]').setValue('s3cret')
    await submitBtn(wrapper).trigger('click')
    await flushAll()

    const [url, opts] = fetchMock.mock.calls[0]
    expect(url).not.toContain('s3cret')
    expect(opts.headers['X-Dashboard-Password']).toBe('s3cret')
  })

  it('never renders the admin bearer token anywhere in the DOM', async () => {
    getToken.mockReturnValue('super-secret-admin-token')
    fetchMock.mockReturnValue(jsonResponse(200, { events: {}, libraries: [], servers: [] }))
    const { wrapper } = await mountView(PublicView, { path: '/myslug' })
    expect(wrapper.html()).not.toContain('super-secret-admin-token')
    // Sent to the backend as a header (intentional — lets admins bypass the
    // password), but that's a request detail, not something the page prints.
    const [, opts] = fetchMock.mock.calls[0]
    expect(opts.headers['Authorization']).toBe('Bearer super-secret-admin-token')
  })

  it('neutralizes a javascript: URL in a server\'s ext_url instead of rendering it as a live link', async () => {
    fetchMock.mockReturnValue(jsonResponse(200, {
      events: {
        [todayStr()]: [{ id: 1, title: 'Evil', server_id: 1, emby_id: '9', library_id: 5, media_type: 'Movie' }],
      },
      libraries: [{ id: 5, server_id: 1, name: 'Movies' }],
      servers: [{ id: 1, name: 'Attack', type: 'emby', ext_url: 'javascript:alert(1)' }],
    }))
    const { wrapper } = await mountView(PublicView, { path: '/myslug' })
    // Open the day panel that has this event so the "view on server" link renders.
    const dayCell = wrapper.findAll('.min-h-\\[70px\\]').find(c => c.text().includes('Evil'))
    await dayCell.trigger('click')
    await flushAll()

    const link = wrapper.find('a[href]')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toBe('#')
    expect(link.attributes('href')).not.toContain('javascript:')
  })

  it('shows the spinner while loading, then the calendar once resolved', async () => {
    let resolveFetch
    fetchMock.mockReturnValue(new Promise(r => { resolveFetch = r }))
    const { wrapper } = await mountView(PublicView, { path: '/myslug' })
    expect(wrapper.find('.fa-spinner').exists()).toBe(true)
    resolveFetch({ status: 200, ok: true, json: () => Promise.resolve({ events: {}, libraries: [], servers: [] }) })
    await flushAll()
    expect(wrapper.find('.fa-spinner').exists()).toBe(false)
  })

  it('falls back to the disabled state when fetch itself throws (network error)', async () => {
    fetchMock.mockRejectedValue(new Error('network down'))
    const { wrapper } = await mountView(PublicView, { path: '/myslug' })
    expect(wrapper.text()).toContain("Le tableau de bord public n'est pas activé.")
  })
})

function submitBtn(wrapper) {
  return [...wrapper.findAll('button')].find(b => b.text() === 'Accéder')
}

function todayStr() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
