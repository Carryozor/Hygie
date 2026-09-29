// src/views/__tests__/App.cov.test.js
//
// App.vue owns the session lifecycle: it starts polling (status.start()) when
// logged in, tears it down on logout/unauthorized, and decides whether the
// authenticated chrome (sidebar/topbar) renders based on route.meta.public.
// A regression here either leaks the public calendar into showing the
// authenticated layout, or leaves background polling running after logout.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mountView, flushAll } from './testUtils.cov.js'
import { setToken, clearToken } from '@/api/tokenStore'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

import api from '@/api/client'
import App from '../../App.vue'

const SidebarStub = { name: 'SidebarStub', template: '<aside class="stub-sidebar" />' }
const TopbarStub  = { name: 'TopbarStub',  template: '<header class="stub-topbar" />' }
const ToastStub   = { name: 'ToastStub',   template: '<div class="stub-toast" />' }

function mockApiDefaults() {
  api.get.mockImplementation(url => {
    if (url === '/settings/media-servers') return Promise.resolve({ data: [] })
    if (url === '/libraries')              return Promise.resolve({ data: [] })
    if (url === '/scheduler/status')       return Promise.resolve({ data: [] })
    if (url === '/logs/unseen-errors-count') return Promise.resolve({ data: { count: 0 } })
    if (url === '/settings')               return Promise.resolve({ data: {} })
    if (url === '/auth/me')                return Promise.resolve({ data: { username: 'admin' } })
    return Promise.resolve({ data: {} })
  })
  api.post.mockResolvedValue({ data: {} })
}

async function mountApp(path = '/') {
  return mountView(App, {
    path,
    stubs: { AppSidebar: SidebarStub, AppTopbar: TopbarStub, ToastNotification: ToastStub },
  })
}

describe('App.vue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearToken()
    mockApiDefaults()
  })
  afterEach(() => clearToken())

  it('renders the authenticated layout (sidebar/topbar) on a protected route', async () => {
    const { wrapper } = await mountApp('/queue')
    expect(wrapper.find('.stub-sidebar').exists()).toBe(true)
    expect(wrapper.find('.stub-topbar').exists()).toBe(true)
  })

  it('hides the sidebar/topbar chrome on the public /:slug route', async () => {
    const { wrapper } = await mountApp('/some-public-slug')
    expect(wrapper.find('.stub-sidebar').exists()).toBe(false)
    expect(wrapper.find('.stub-topbar').exists()).toBe(false)
    expect(wrapper.find('.stub-toast').exists()).toBe(true) // toast always renders
  })

  it('hides the chrome on /login', async () => {
    const { wrapper } = await mountApp('/login')
    expect(wrapper.find('.stub-sidebar').exists()).toBe(false)
    expect(wrapper.find('.stub-topbar').exists()).toBe(false)
  })

  it('starts the session (fetchMe/settings/status polling) on mount when already logged in', async () => {
    setToken('tok')
    await mountApp('/queue')
    await flushAll()
    // auth.fetchMe -> GET /auth/me ; settings.fetch -> GET /settings ;
    // status.start -> servers.fetch (GET /settings/media-servers + /libraries) + fetchScheduler
    expect(api.get).toHaveBeenCalledWith('/auth/me')
    expect(api.get).toHaveBeenCalledWith('/settings')
    expect(api.get).toHaveBeenCalledWith('/scheduler/status')
  })

  it('does NOT start session polling on mount when logged out', async () => {
    await mountApp('/login')
    await flushAll()
    expect(api.get).not.toHaveBeenCalledWith('/auth/me')
    expect(api.get).not.toHaveBeenCalledWith('/scheduler/status')
  })

  it('redirects to /login on hygie:unauthorized while on a protected route', async () => {
    const { router } = await mountApp('/queue')
    const pushSpy = vi.spyOn(router, 'push')
    window.dispatchEvent(new Event('hygie:unauthorized'))
    await flushAll()
    expect(pushSpy).toHaveBeenCalledWith('/login')
  })

  it('does NOT redirect on hygie:unauthorized while already on a public route', async () => {
    const { router } = await mountApp('/some-public-slug')
    const pushSpy = vi.spyOn(router, 'push')
    window.dispatchEvent(new Event('hygie:unauthorized'))
    await flushAll()
    expect(pushSpy).not.toHaveBeenCalled()
  })

  it('unauthorized handling calls auth.logout (POST /auth/logout)', async () => {
    setToken('tok')
    await mountApp('/queue')
    await flushAll()
    api.post.mockClear()
    window.dispatchEvent(new Event('hygie:unauthorized'))
    await flushAll()
    expect(api.post).toHaveBeenCalledWith('/auth/logout', expect.anything())
  })

  it('removes the hygie:unauthorized listener on unmount (no stale handler after navigating away)', async () => {
    const { wrapper, router } = await mountApp('/queue')
    wrapper.unmount()
    const pushSpy = vi.spyOn(router, 'push')
    window.dispatchEvent(new Event('hygie:unauthorized'))
    await flushAll()
    expect(pushSpy).not.toHaveBeenCalled()
  })
})
