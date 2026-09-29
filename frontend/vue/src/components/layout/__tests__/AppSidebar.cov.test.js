// src/components/layout/__tests__/AppSidebar.cov.test.js
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createWebHistory } from 'vue-router'
import { i18n } from '@/i18n'

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
vi.mock('@/stores/servers', () => ({ useServersStore: () => ({ servers: [], librariesForServer: () => [] }) }))

let authState = { isLoggedIn: true }
vi.mock('@/stores/auth', () => ({ useAuthStore: () => authState }))

const settingsFetch = vi.fn().mockResolvedValue(undefined)
let settingsState = { settings: {}, fetch: settingsFetch }
vi.mock('@/stores/settings', () => ({ useSettingsStore: () => settingsState }))

let statusState = {
  scanNext: null, deletionNext: null, scanRunning: false, deletionRunning: false,
  hasUnseenErrors: false, logoStatus: 'none', serverResults: [], fetchScheduler: vi.fn().mockResolvedValue(undefined),
}
vi.mock('@/stores/status', () => ({ useStatusStore: () => statusState }))

import api from '@/api/client'
import AppSidebar from '../AppSidebar.vue'

async function mountSidebar() {
  const router = createRouter({ history: createWebHistory(), routes: [{ path: '/', name: 'dashboard', component: { template: '<div/>' } }] })
  router.push('/')
  await router.isReady()
  const wrapper = mount(AppSidebar, { global: { plugins: [router, i18n] } })
  await flushPromises()
  return wrapper
}

describe('AppSidebar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    authState = { isLoggedIn: true }
    settingsState = { settings: {}, fetch: settingsFetch }
    statusState = {
      scanNext: null, deletionNext: null, scanRunning: false, deletionRunning: false,
      hasUnseenErrors: false, logoStatus: 'none', serverResults: [], fetchScheduler: vi.fn().mockResolvedValue(undefined),
    }
    api.get.mockResolvedValue({ data: { version: '4.3.6' } })
    api.post.mockResolvedValue({ data: {} })
  })
  afterEach(() => vi.useRealTimers())

  it('does nothing on mount when not logged in (no version fetch, no settings fetch)', async () => {
    authState = { isLoggedIn: false }
    await mountSidebar()
    expect(settingsFetch).not.toHaveBeenCalled()
    expect(api.get).not.toHaveBeenCalled()
  })

  it('fetches settings and the version string on mount when logged in', async () => {
    const wrapper = await mountSidebar()
    expect(settingsFetch).toHaveBeenCalled()
    expect(wrapper.text()).toContain('4.3.6')
  })

  it('does not crash when the version fetch fails', async () => {
    api.get.mockRejectedValueOnce(new Error('down'))
    const wrapper = await mountSidebar()
    expect(wrapper.find('aside').exists()).toBe(true)
  })

  it('renders one nav link per configured section', async () => {
    const wrapper = await mountSidebar()
    const links = wrapper.findAllComponents({ name: 'RouterLink' })
    expect(links.length).toBeGreaterThanOrEqual(7)
  })

  it('shows "dry run active" styling when settings.dry_run is the string "true"', async () => {
    settingsState = { settings: { dry_run: 'true' }, fetch: settingsFetch }
    const wrapper = await mountSidebar()
    expect(wrapper.text()).toContain(i18n.global.t('sidebar.dryRunActive'))
  })

  it('shows the normal dry-run label when dry_run is false/unset', async () => {
    const wrapper = await mountSidebar()
    expect(wrapper.text()).toContain(i18n.global.t('sidebar.dryRun'))
  })

  it('toggleDryRun posts the inverse value and refetches settings', async () => {
    settingsState = { settings: { dry_run: 'false' }, fetch: settingsFetch }
    const wrapper = await mountSidebar()
    const dryRunBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('sidebar.dryRun')))
    await dryRunBtn.trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith('/settings', { dry_run: 'true' })
    expect(settingsFetch).toHaveBeenCalledTimes(2) // once on mount, once after toggle
  })

  it('does not crash when toggling dry run fails', async () => {
    api.post.mockRejectedValueOnce(new Error('down'))
    const wrapper = await mountSidebar()
    const dryRunBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('sidebar.dryRun')))
    await dryRunBtn.trigger('click')
    await flushPromises()
    expect(wrapper.find('aside').exists()).toBe(true)
  })

  it('hides the scan progress block when there is no scanNext and no scan running', async () => {
    const wrapper = await mountSidebar()
    expect(wrapper.text()).not.toContain(i18n.global.t('sidebar.nextScan'))
  })

  it('shows the scan countdown when scanNext is set', async () => {
    statusState.scanNext = new Date(Date.now() + 3600000).toISOString()
    const wrapper = await mountSidebar()
    expect(wrapper.text()).toContain(i18n.global.t('sidebar.nextScan'))
  })

  it('shows "scan running" text and disables the trigger button while scanRunning', async () => {
    statusState.scanNext = new Date(Date.now() + 3600000).toISOString()
    statusState.scanRunning = true
    const wrapper = await mountSidebar()
    expect(wrapper.text()).toContain(i18n.global.t('sidebar.scanRunning'))
  })

  it('triggerScan posts /scan/trigger and refetches the scheduler', async () => {
    statusState.scanNext = new Date(Date.now() + 3600000).toISOString()
    const wrapper = await mountSidebar()
    const scanBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('sidebar.nextScan')))
    await scanBtn.trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith('/scan/trigger')
    expect(statusState.fetchScheduler).toHaveBeenCalled()
  })

  it('does not crash when triggering a scan fails (already running, etc.)', async () => {
    statusState.scanNext = new Date(Date.now() + 3600000).toISOString()
    api.post.mockRejectedValueOnce(new Error('already running'))
    const wrapper = await mountSidebar()
    const scanBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('sidebar.nextScan')))
    await scanBtn.trigger('click')
    await flushPromises()
    expect(statusState.fetchScheduler).toHaveBeenCalled()
  })

  it('shows the deletion countdown when deletionNext is set, and triggerDeletion posts /deletion/trigger', async () => {
    statusState.deletionNext = new Date(Date.now() + 1800000).toISOString()
    const wrapper = await mountSidebar()
    expect(wrapper.text()).toContain(i18n.global.t('sidebar.nextDeletion'))
    const delBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('sidebar.nextDeletion')))
    await delBtn.trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith('/deletion/trigger')
  })

  it('formats a countdown under an hour as minutes only', async () => {
    statusState.scanNext = new Date(Date.now() + 20 * 60000).toISOString()
    const wrapper = await mountSidebar()
    expect(wrapper.text()).toMatch(/\d+m/)
  })

  it('shows "imminent" when the countdown target is in the past', async () => {
    statusState.scanNext = new Date(Date.now() - 1000).toISOString()
    const wrapper = await mountSidebar()
    expect(wrapper.text()).toContain(i18n.global.t('days.imminent'))
  })

  it('unmounting clears the internal clock interval (no leaked timer)', async () => {
    vi.useFakeTimers()
    const wrapper = await mountSidebar()
    const clearSpy = vi.spyOn(globalThis, 'clearInterval')
    wrapper.unmount()
    expect(clearSpy).toHaveBeenCalled()
    clearSpy.mockRestore()
  })
})
