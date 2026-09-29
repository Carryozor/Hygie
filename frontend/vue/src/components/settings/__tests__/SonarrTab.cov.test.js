// src/components/settings/__tests__/SonarrTab.cov.test.js
//
// SonarrTab stores instances as a JSON blob in form.sonarr_servers, and
// each instance's api_key is masked ('***') — reveal goes through
// useRevealSetting -> GET /settings/reveal/sonarr_servers, returning the
// full decrypted server list, from which this instance's key is picked
// out by id. We mock only @/api/client and let the real composable run.
//
// NOTE: SonarrTab builds its instance list in onMounted() (synchronous body,
// no top-level await), but Vue still defers the resulting DOM patch to a
// microtask — so every mount() here is followed by flushPromises() before
// any assertion on rendered content.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { i18n } from '@/i18n'

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
import api from '@/api/client'
import SonarrTab from '../SonarrTab.vue'

async function mountTab(sonarrServers, formOverrides = {}) {
  const form = { sonarr_servers: JSON.stringify(sonarrServers), sonarr_url: '', sonarr_api_key: '', ...formOverrides }
  const wrapper = mount(SonarrTab, { props: { form }, global: { plugins: [i18n] } })
  await flushPromises()
  return { wrapper, form }
}

describe('SonarrTab', () => {
  beforeEach(() => vi.clearAllMocks())

  it('parses form.sonarr_servers JSON into the instance list on mount', async () => {
    const { wrapper } = await mountTab([{ id: '1', name: 'Sonarr Main', url: 'http://sonarr:8989', api_key: '***', enabled: true }])
    expect(wrapper.find('input[type=text]').element.value).toBe('Sonarr Main')
    expect(wrapper.find('input[type=password]').element.value).toBe('***')
  })

  it('falls back to the legacy single sonarr_url/sonarr_api_key when sonarr_servers is empty', async () => {
    const { wrapper } = await mountTab([], { sonarr_url: 'http://legacy:7878', sonarr_api_key: 'legacykey' })
    expect(wrapper.find('input[type=url]').element.value).toBe('http://legacy:7878')
  })

  it('addServer appends a new blank instance and syncs it into form.sonarr_servers as JSON', async () => {
    const { wrapper, form } = await mountTab([])
    const addBtn = wrapper.find('button.w-full')
    await addBtn.trigger('click')
    const parsed = JSON.parse(form.sonarr_servers)
    expect(parsed).toHaveLength(1)
    expect(parsed[0].name).toBe('Sonarr')
  })

  it('removeServer removes the instance and clears legacy fields when the list becomes empty', async () => {
    const { wrapper, form } = await mountTab(
      [{ id: '1', name: 'R', url: '', api_key: '', enabled: true }],
      { sonarr_url: 'old', sonarr_api_key: 'oldkey' },
    )
    const removeBtn = wrapper.find('button[title]')
    await removeBtn.trigger('click')
    expect(JSON.parse(form.sonarr_servers)).toEqual([])
    expect(form.sonarr_url).toBe('')
    expect(form.sonarr_api_key).toBe('')
  })

  it('editing the instance name syncs the change back into form.sonarr_servers', async () => {
    const { wrapper, form } = await mountTab([{ id: '1', name: 'Sonarr', url: '', api_key: '', enabled: true }])
    const nameInput = wrapper.find('input[type=text]')
    await nameInput.setValue('Sonarr 4K')
    expect(JSON.parse(form.sonarr_servers)[0].name).toBe('Sonarr 4K')
  })

  it('reveals the real api_key for the matching instance id via /settings/reveal/sonarr_servers', async () => {
    api.get.mockResolvedValueOnce({
      data: { value: [{ id: '1', api_key: 'REAL-SONARR-KEY' }, { id: '2', api_key: 'OTHER-KEY' }] },
    })
    const { wrapper } = await mountTab([{ id: '1', name: 'Sonarr', url: '', api_key: '***', enabled: true }])
    const eyeBtn = wrapper.findAll('button').find(b => b.find('.fa-eye').exists())
    await eyeBtn.trigger('click')
    await flushPromises()
    expect(api.get).toHaveBeenCalledWith('/settings/reveal/sonarr_servers')
    const revealedInput = wrapper.findAll('input').find(i => i.element.value === 'REAL-SONARR-KEY')
    expect(revealedInput).toBeTruthy()
  })

  it('keeps the mask when reveal succeeds but no matching id is found in the response', async () => {
    api.get.mockResolvedValueOnce({ data: { value: [{ id: 'different-id', api_key: 'X' }] } })
    const { wrapper } = await mountTab([{ id: '1', name: 'Sonarr', url: '', api_key: '***', enabled: true }])
    const eyeBtn = wrapper.findAll('button').find(b => b.find('.fa-eye').exists())
    await eyeBtn.trigger('click')
    await flushPromises()
    const keyInput = wrapper.find('input[type=password], input.flex-1.field.font-mono')
    expect(keyInput.element.value).toBe('***')
  })

  it('testInstance posts type=sonarr with this instance url/api_key and shows ok/error state', async () => {
    api.post.mockResolvedValueOnce({ data: { ok: true, message: 'Connected' } })
    const { wrapper } = await mountTab([{ id: '1', name: 'Sonarr', url: 'http://sonarr:8989', api_key: 'k', enabled: true }])
    const testBtn = wrapper.findAll('button').find(b => /^(Tester|✓ OK|…)$/.test(b.text()))
    await testBtn.trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith('/settings/test-arr', { type: 'sonarr', url: 'http://sonarr:8989', api_key: 'k' })
    expect(wrapper.text()).toContain('Connected')
  })

  it('testInstance shows the error state (no crash) when the request rejects', async () => {
    api.post.mockRejectedValueOnce(new Error('timeout'))
    const { wrapper } = await mountTab([{ id: '1', name: 'Sonarr', url: 'http://sonarr:8989', api_key: 'k', enabled: true }])
    const testBtn = wrapper.findAll('button').find(b => /^(Tester|✓ OK|…)$/.test(b.text()))
    await testBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Échec')
  })

  it('an external prop change to form.sonarr_servers (e.g. after a fresh GET /settings) updates the local list', async () => {
    const { wrapper, form } = await mountTab([{ id: '1', name: 'Sonarr', url: '', api_key: '', enabled: true }])
    form.sonarr_servers = JSON.stringify([{ id: '2', name: 'Sonarr Externally Updated', url: '', api_key: '', enabled: true }])
    await wrapper.setProps({ form: { ...form } })
    await flushPromises()
    expect(wrapper.find('input[type=text]').element.value).toBe('Sonarr Externally Updated')
  })

  it('does not re-parse (ignores) the watch callback for its own sync() writes (self-update guard)', async () => {
    const { wrapper } = await mountTab([{ id: '1', name: 'Sonarr', url: '', api_key: '', enabled: true }])
    const nameInput = wrapper.find('input[type=text]')
    await nameInput.setValue('Renamed')
    await flushPromises()
    // If the self-update guard failed, the watcher would re-parse the JSON it just wrote
    // and potentially reset local state — the input should still reflect what we typed.
    expect(wrapper.find('input[type=text]').element.value).toBe('Renamed')
  })
})
