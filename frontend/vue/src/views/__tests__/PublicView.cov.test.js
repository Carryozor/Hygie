// src/views/__tests__/PublicView.cov.test.js
//
// PublicView is the unauthenticated route — its one security requirement is
// that it never leaks an internal/unsafe URL or the admin token into the
// rendered DOM. It talks to the backend via global fetch (not api/client.js),
// so we mock window.fetch directly.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { DOMWrapper } from '@vue/test-utils'
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

  it('falls back to the disabled state on a generic non-ok status not otherwise handled', async () => {
    fetchMock.mockReturnValue(jsonResponse(500, {}))
    const { wrapper } = await mountView(PublicView, { path: '/myslug' })
    expect(wrapper.text()).toContain("Le tableau de bord public n'est pas activé.")
  })

  describe('with a loaded multi-server dashboard', () => {
    function multiServerData() {
      const day = todayStr()
      return {
        language: 'fr',
        events: {
          [day]: [
            { id: 1, title: 'Dune', server_id: 1, emby_id: '9', library_id: 5, library_name: 'Films', media_type: 'Movie', tmdb_id: 438631, poster_url: 'https://img/dune.jpg' },
            { id: 2, title: 'The Wire', server_id: 2, emby_id: '3', library_id: 6, library_name: 'Séries', media_type: 'Series' },
          ],
        },
        libraries: [{ id: 5, server_id: 1, name: 'Films' }, { id: 6, server_id: 2, name: 'Séries' }],
        servers: [
          { id: 1, name: 'MyEmby', type: 'emby', ext_url: 'https://emby.example.com/' },
          { id: 2, name: 'MyJelly', type: 'jellyfin', ext_url: 'https://jelly.example.com' },
        ],
      }
    }

    async function mountLoaded() {
      fetchMock.mockReturnValue(jsonResponse(200, multiServerData()))
      return mountView(PublicView, { path: '/myslug' })
    }

    it('switches the displayed language and persists it to localStorage', async () => {
      const { wrapper } = await mountLoaded()
      expect(wrapper.text()).toContain('Prochaines suppressions')
      const enBtn = [...wrapper.findAll('button')].find(b => b.text() === 'EN')
      await enBtn.trigger('click')
      expect(wrapper.text()).toContain('Upcoming deletions')
      expect(localStorage.getItem('hygie_public_lang')).toBe('en')
    })

    it('filters events by server tab, then back to "all"', async () => {
      const { wrapper } = await mountLoaded()
      expect(wrapper.text()).toContain('2 planifié(s)')
      const embyTab = [...wrapper.findAll('button')].find(b => b.text().includes('MyEmby'))
      await embyTab.trigger('click')
      expect(wrapper.text()).toContain('1 planifié(s)')
      const allTab = [...wrapper.findAll('button')].find(b => b.text() === 'Tous')
      await allTab.trigger('click')
      expect(wrapper.text()).toContain('2 planifié(s)')
    })

    it('navigates prev/next month and jumps back to today', async () => {
      const { wrapper } = await mountLoaded()
      const monthLabel = () => wrapper.find('h2.font-semibold.capitalize').text()
      const initial = monthLabel()
      // Two ".mb-4" elements exist (page header + month-nav bar); only the
      // month-nav one has <button> as DIRECT children (prev/next either
      // side of a <div>), so this selector still isolates just those two.
      const [prevBtn, nextBtn] = wrapper.findAll('.mb-4 > button')
      await nextBtn.trigger('click')
      expect(monthLabel()).not.toBe(initial)
      // "today" quick-jump button appears once we've navigated away
      const todayBtn = [...wrapper.findAll('button')].find(b => b.text() === "Aujourd'hui")
      expect(todayBtn).toBeTruthy()
      await todayBtn.trigger('click')
      expect(monthLabel()).toBe(initial)
      await prevBtn.trigger('click')
      expect(monthLabel()).not.toBe(initial)
    })

    it('opens the day panel, then closes it via the close button', async () => {
      const { wrapper } = await mountLoaded()
      const dayCell = wrapper.findAll('.min-h-\\[70px\\]').find(c => c.text().includes('Dune'))
      await dayCell.trigger('click')
      const panel = wrapper.findAll('.px-5.py-3.group')
      expect(panel.length).toBe(2)
      const closeBtn = wrapper.find('.fa-xmark').element.closest('button')
      await new DOMWrapper(closeBtn).trigger('click')
      expect(wrapper.find('.mt-4.bg-\\[var\\(--bg2\\)\\]').exists()).toBe(false)
    })

    it('opens the TMDB link for an item with tmdb_id, and hides its broken poster on img error', async () => {
      const openSpy = vi.spyOn(window, 'open').mockImplementation(() => {})
      const { wrapper } = await mountLoaded()
      const dayCell = wrapper.findAll('.min-h-\\[70px\\]').find(c => c.text().includes('Dune'))
      await dayCell.trigger('click')

      const img = wrapper.find('img')
      expect(img.exists()).toBe(true)
      await img.trigger('error')
      expect(img.element.style.display).toBe('none')

      const duneRow = wrapper.findAll('.px-5.py-3.group').find(r => r.text().includes('Dune'))
      await duneRow.trigger('click')
      expect(openSpy).toHaveBeenCalledWith('https://www.themoviedb.org/movie/438631', '_blank', 'noopener')
      openSpy.mockRestore()
    })

    it('builds the correct "view on server" deep link per server type (emby vs jellyfin)', async () => {
      const { wrapper } = await mountLoaded()
      const dayCell = wrapper.findAll('.min-h-\\[70px\\]').find(c => c.text().includes('Dune'))
      await dayCell.trigger('click')

      const links = wrapper.findAll('a[href]')
      const embyLink = links.find(a => a.attributes('href').includes('emby.example.com'))
      expect(embyLink.attributes('href')).toBe('https://emby.example.com/web/index.html#!/item?id=9')
      const jellyLink = links.find(a => a.attributes('href').includes('jelly.example.com'))
      expect(jellyLink.attributes('href')).toBe('https://jelly.example.com/web/index.html#!/details?id=3')
    })

    it('builds a Plex rating-key deep link for a plex server', async () => {
      fetchMock.mockReturnValue(jsonResponse(200, {
        language: 'fr',
        events: { [todayStr()]: [{ id: 1, title: 'Dune', server_id: 1, emby_id: '42', library_id: 5, library_name: 'Films', media_type: 'Movie' }] },
        libraries: [{ id: 5, server_id: 1, name: 'Films' }],
        servers: [{ id: 1, name: 'MyPlex', type: 'plex', ext_url: 'https://plex.example.com' }],
      }))
      const { wrapper } = await mountView(PublicView, { path: '/myslug' })
      const dayCell = wrapper.findAll('.min-h-\\[70px\\]').find(c => c.text().includes('Dune'))
      await dayCell.trigger('click')
      const link = wrapper.find('a[href*="plex.example.com"]')
      expect(link.attributes('href')).toBe('https://plex.example.com/web/index.html#!/item?key=/library/metadata/42')
    })
  })
})

function submitBtn(wrapper) {
  return [...wrapper.findAll('button')].find(b => b.text() === 'Accéder')
}

function todayStr() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
