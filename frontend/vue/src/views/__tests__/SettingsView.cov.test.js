// src/views/__tests__/SettingsView.cov.test.js
//
// SettingsView is not destructive (no delete), but a broken save silently
// eating a backend error would leave admins thinking their config (SSRF
// guards, retention days, etc.) was applied when it wasn't. All 8 tab
// components are stubbed — they're each a separate, large component outside
// this file's scope; here we only check SettingsView's own tab-switching,
// save success/error, and which tabs hide the Save button.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mountView, flushAll } from './testUtils.cov.js'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

import api from '@/api/client'
import SettingsView from '../SettingsView.vue'

function makeTabStub(name) {
  return { name, props: ['form'], template: '<div class="tab-stub" />' }
}
const GeneralTabStub = makeTabStub('GeneralTabStub')
const STUBS = {
  GeneralTab: GeneralTabStub, ServersTab: makeTabStub('ServersTabStub'), SeerrTab: makeTabStub('SeerrTabStub'),
  RadarrTab: makeTabStub('RadarrTabStub'), SonarrTab: makeTabStub('SonarrTabStub'), QbitTab: makeTabStub('QbitTabStub'),
  DiscordTab: makeTabStub('DiscordTabStub'), DatabaseTab: makeTabStub('DatabaseTabStub'),
}

async function mountSettings(settings = {}) {
  api.get.mockResolvedValue({ data: settings })
  return mountView(SettingsView, { path: '/settings', stubs: STUBS })
}

describe('SettingsView', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => vi.useRealTimers())

  it('defaults to the General tab and switches tabs on click', async () => {
    const { wrapper } = await mountSettings()
    const tabs = wrapper.findAll('button').filter(b => b.text().length > 0 && !['Enregistrer', 'Enregistrement…'].includes(b.text()))
    const serversTab = tabs.find(b => b.text() === 'Serveurs')
    await serversTab.trigger('click')
    // Save button hides on the Servers tab
    expect([...wrapper.findAll('button')].some(b => b.text() === 'Enregistrer')).toBe(false)
  })

  it('hides the Save button on the Database tab too', async () => {
    const { wrapper } = await mountSettings()
    const dbTab = [...wrapper.findAll('button')].find(b => b.text() === 'Base de données')
    await dbTab.trigger('click')
    expect([...wrapper.findAll('button')].some(b => b.text() === 'Enregistrer')).toBe(false)
  })

  it('shows the Save button again after switching back to General', async () => {
    const { wrapper } = await mountSettings()
    const serversTab = [...wrapper.findAll('button')].find(b => b.text() === 'Serveurs')
    await serversTab.trigger('click')
    const generalTab = [...wrapper.findAll('button')].find(b => b.text() === 'Général')
    await generalTab.trigger('click')
    expect([...wrapper.findAll('button')].some(b => b.text() === 'Enregistrer')).toBe(true)
  })

  it('shows the saved confirmation on a successful save, and clears it after 3s', async () => {
    const { wrapper } = await mountSettings()
    api.post.mockResolvedValue({ data: {} })
    const saveBtn = [...wrapper.findAll('button')].find(b => b.text() === 'Enregistrer')

    vi.useFakeTimers()
    await saveBtn.trigger('click')
    await vi.advanceTimersByTimeAsync(0) // let the POST promise microtask settle
    expect(wrapper.text()).toContain('Paramètres sauvegardés.')

    await vi.advanceTimersByTimeAsync(3000)
    expect(wrapper.text()).not.toContain('Paramètres sauvegardés.')
  })

  it('shows the backend error detail on a failed save, and clears it after 6s', async () => {
    const { wrapper } = await mountSettings()
    api.post.mockRejectedValue({ response: { data: { detail: 'URL SSRF bloquée' } } })
    const saveBtn = [...wrapper.findAll('button')].find(b => b.text() === 'Enregistrer')

    vi.useFakeTimers()
    await saveBtn.trigger('click')
    await vi.advanceTimersByTimeAsync(0)
    expect(wrapper.text()).toContain('Échec de la sauvegarde : URL SSRF bloquée')

    await vi.advanceTimersByTimeAsync(6000)
    expect(wrapper.text()).not.toContain('Échec de la sauvegarde')
  })

  it('falls back to a generic error message when the backend gives no detail', async () => {
    const { wrapper } = await mountSettings()
    api.post.mockRejectedValue(new Error('network down'))
    const saveBtn = [...wrapper.findAll('button')].find(b => b.text() === 'Enregistrer')
    await saveBtn.trigger('click')
    await flushAll()
    expect(wrapper.text()).toContain('Échec de la sauvegarde : network down')
  })

  it('disables the Save button and shows "Enregistrement…" while saving', async () => {
    let resolveSave
    const { wrapper } = await mountSettings()
    api.post.mockImplementation(() => new Promise(r => { resolveSave = r }))
    const saveBtn = [...wrapper.findAll('button')].find(b => b.text() === 'Enregistrer')
    await saveBtn.trigger('click')
    await flushAll()
    const btn = [...wrapper.findAll('button')].find(b => b.text().includes('Enregistrement'))
    expect(btn.attributes('disabled')).toBeDefined()
    resolveSave({ data: {} })
    await flushAll()
  })

  it('normalizes boolean-string settings (dry_run: "true") into the form passed to the General tab', async () => {
    const { wrapper } = await mountSettings({ dry_run: 'true', log_level: 'DEBUG' })
    const generalTab = wrapper.findComponent(GeneralTabStub)
    expect(generalTab.props('form').dry_run).toBe(true)
    expect(generalTab.props('form').log_level).toBe('DEBUG')
  })

  it('POSTs the current form values back to /settings on save', async () => {
    const { wrapper } = await mountSettings({ log_level: 'WARNING', deleted_retention_days: '45' })
    api.post.mockResolvedValue({ data: {} })
    const saveBtn = [...wrapper.findAll('button')].find(b => b.text() === 'Enregistrer')
    await saveBtn.trigger('click')
    await flushAll()
    const [, payload] = api.post.mock.calls[0]
    expect(payload.log_level).toBe('WARNING')
    expect(payload.deleted_retention_days).toBe('45')
  })
})
