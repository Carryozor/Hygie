// src/components/layout/__tests__/AppTopbar.cov.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createWebHistory } from 'vue-router'
import { i18n } from '@/i18n'

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

const { setLocale, logout } = vi.hoisted(() => ({ setLocale: vi.fn(), logout: vi.fn() }))

vi.mock('@/i18n', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, setLocale }
})
vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ username: 'alice', logout }),
}))

import api from '@/api/client'
import AppTopbar from '../AppTopbar.vue'

async function mountTopbar(routeName = 'dashboard') {
  const router = createRouter({
    history: createWebHistory(),
    routes: [{ path: '/', name: routeName, component: { template: '<div/>' } }],
  })
  router.push('/')
  await router.isReady()
  const wrapper = mount(AppTopbar, { global: { plugins: [router, i18n] } })
  await flushPromises()
  return { wrapper, router }
}

describe('AppTopbar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('shows the logged-in username', async () => {
    api.get.mockResolvedValueOnce({ data: { ui_language: 'fr' } })
    const { wrapper } = await mountTopbar()
    expect(wrapper.text()).toContain('alice')
  })

  it('resolves the page title from the current route name', async () => {
    api.get.mockResolvedValueOnce({ data: {} })
    const { wrapper } = await mountTopbar('queue')
    expect(wrapper.find('h1').text()).toBe(i18n.global.t('nav.queue'))
  })

  it('falls back to "Hygie" for an unknown route name', async () => {
    api.get.mockResolvedValueOnce({ data: {} })
    const { wrapper } = await mountTopbar('some_unmapped_route')
    expect(wrapper.find('h1').text()).toBe('Hygie')
  })

  it('fetches and applies the saved UI language on mount', async () => {
    api.get.mockResolvedValueOnce({ data: { ui_language: 'en' } })
    const { wrapper } = await mountTopbar()
    expect(wrapper.find('select').element.value).toBe('en')
    expect(setLocale).toHaveBeenCalledWith('en')
  })

  it('defaults to fr when GET /settings fails', async () => {
    api.get.mockRejectedValueOnce(new Error('down'))
    const { wrapper } = await mountTopbar()
    expect(wrapper.find('select').element.value).toBe('fr')
  })

  it('changing the language select calls setLocale and persists it via POST /settings', async () => {
    api.get.mockResolvedValueOnce({ data: {} })
    api.post.mockResolvedValueOnce({ data: {} })
    const { wrapper } = await mountTopbar()
    await wrapper.find('select').setValue('de')
    await flushPromises()
    expect(setLocale).toHaveBeenCalledWith('de')
    expect(api.post).toHaveBeenCalledWith('/settings', { ui_language: 'de' })
  })

  it('does not crash when saving the language preference fails', async () => {
    api.get.mockResolvedValueOnce({ data: {} })
    api.post.mockRejectedValueOnce(new Error('network'))
    const { wrapper } = await mountTopbar()
    await wrapper.find('select').setValue('es')
    await flushPromises()
    expect(setLocale).toHaveBeenCalledWith('es')
  })

  it('clicking logout calls auth.logout() and navigates to /login', async () => {
    api.get.mockResolvedValueOnce({ data: {} })
    const { wrapper, router } = await mountTopbar()
    const pushSpy = vi.spyOn(router, 'push')
    await wrapper.find('button[title]').trigger('click')
    expect(logout).toHaveBeenCalled()
    expect(pushSpy).toHaveBeenCalledWith('/login')
  })
})
