// src/components/settings/__tests__/ServersTab.cov.test.js
//
// ServersTab holds the media-server API key masking/reveal contract:
// GET /settings/media-servers returns api_key='***' by default, the real
// value is only fetched via POST/GET .../reveal, and the backend (see
// backend/routers/settings.py PUT /media-servers/{id}, line ~313: "if
// body.api_key is not None and body.api_key != _MASK") intentionally ignores
// '***' as a no-op so it never overwrites a real stored key. These tests
// verify the front never bypasses that contract (no eager reveal, no key
// echoed back before an explicit user action).
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { i18n } from '@/i18n'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))
const serversFetch = vi.fn().mockResolvedValue(undefined)
vi.mock('@/stores/servers', () => ({ useServersStore: () => ({ fetch: serversFetch }) }))
vi.mock('@/stores/settings', () => ({ useSettingsStore: () => ({ save: vi.fn().mockResolvedValue(undefined) }) }))
const checkServerHealth = vi.fn().mockResolvedValue(undefined)
vi.mock('@/stores/status', () => ({ useStatusStore: () => ({ checkServerHealth }) }))

import api from '@/api/client'
import ServersTab from '../ServersTab.vue'

const MASKED_SERVER = {
  id: '5', name: 'Mon Emby', type: 'emby', url: 'http://emby:8096',
  api_key: '***', ext_url: '', enabled: true,
}

// ServersTab keeps its auto-detect timers in a module-level Map: a wrapper left
// mounted leaks a real 800ms timer into whichever test runs next (flaky on slow CI).
const mounted = []

function mountTab(formOverrides = {}) {
  const wrapper = mount(ServersTab, {
    props: {
      form: {
        plex_webhook_secret: '', plex_overlay_enabled: false,
        emby_leaving_soon_overlay: false, emby_leaving_soon_collection: '', emby_leaving_soon_days: 30,
        ...formOverrides,
      },
    },
    global: { plugins: [i18n] },
  })
  mounted.push(wrapper)
  return wrapper
}

describe('ServersTab', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })
  afterEach(() => {
    mounted.splice(0).forEach(w => w.unmount())
  })

  it('loads servers on mount and renders the masked api_key without revealing it', async () => {
    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER] })
    api.post.mockResolvedValue({ data: { ok: true } }) // silentTest background call
    const wrapper = mountTab()
    await flushPromises()
    const keyInput = wrapper.find('input[type=password]')
    expect(keyInput.exists()).toBe(true)
    expect(keyInput.element.value).toBe('***')
    // Never called /reveal just from loading the list
    expect(api.get).not.toHaveBeenCalledWith(expect.stringContaining('/reveal'))
  })

  it('clicking the reveal (eye) button fetches the real key and switches the input to text', async () => {
    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER] })
    api.post.mockResolvedValue({ data: { ok: true } })
    api.get.mockResolvedValueOnce({ data: { api_key: 'REAL-SECRET-KEY' } }) // GET .../reveal
    const wrapper = mountTab()
    await flushPromises()
    const eyeBtn = wrapper.findAll('button').find(b => b.find('.fa-eye').exists())
    await eyeBtn.trigger('click')
    await flushPromises()
    expect(api.get).toHaveBeenCalledWith('/settings/media-servers/5/reveal')
    // After reveal the field switches type to text and shows the real value
    const revealedInput = wrapper.findAll('input').find(i => i.element.value === 'REAL-SECRET-KEY')
    expect(revealedInput).toBeTruthy()
    expect(revealedInput.attributes('type')).toBe('text')
  })

  it('toggling reveal a second time (already revealed) does not call the API again', async () => {
    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER] })
    api.post.mockResolvedValue({ data: { ok: true } })
    api.get.mockResolvedValueOnce({ data: { api_key: 'REAL-SECRET-KEY' } })
    const wrapper = mountTab()
    await flushPromises()
    const eyeBtn = wrapper.findAll('button').find(b => b.find('.fa-eye').exists())
    await eyeBtn.trigger('click')
    await flushPromises()
    expect(api.get).toHaveBeenCalledTimes(2) // list + reveal
    const eyeSlashBtn = wrapper.findAll('button').find(b => b.find('.fa-eye-slash').exists())
    await eyeSlashBtn.trigger('click') // hide again
    await flushPromises()
    expect(api.get).toHaveBeenCalledTimes(2) // no extra call just hiding
  })

  it('reveal failure keeps the masked value and does not switch to text', async () => {
    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER] })
    api.post.mockResolvedValue({ data: { ok: true } })
    api.get.mockRejectedValueOnce(new Error('403'))
    const wrapper = mountTab()
    await flushPromises()
    const eyeBtn = wrapper.findAll('button').find(b => b.find('.fa-eye').exists())
    await eyeBtn.trigger('click')
    await flushPromises()
    const keyInput = wrapper.find('input[type=password]')
    expect(keyInput.exists()).toBe(true)
    expect(keyInput.element.value).toBe('***')
  })

  it('saveServers sends the still-masked "***" placeholder unchanged when the user never revealed/edited it (backend treats it as no-op)', async () => {
    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER] }) // initial load
    api.post.mockResolvedValue({ data: { ok: true } })       // silentTest
    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER] }) // saveServers' current-list re-fetch
    api.put.mockResolvedValueOnce({ data: {} })
    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER] }) // loadServers() after save
    const wrapper = mountTab()
    await flushPromises()
    const saveBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.servers.save')))
    await saveBtn.trigger('click')
    await flushPromises()
    expect(api.put).toHaveBeenCalledWith('/settings/media-servers/5', expect.objectContaining({ api_key: '***' }))
  })

  it('saveServers sends the real revealed key when the user actually edited it', async () => {
    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER] })
    api.post.mockResolvedValue({ data: { ok: true } })
    api.get.mockResolvedValueOnce({ data: { api_key: 'OLD-KEY' } }) // reveal
    const wrapper = mountTab()
    await flushPromises()
    const eyeBtn = wrapper.findAll('button').find(b => b.find('.fa-eye').exists())
    await eyeBtn.trigger('click')
    await flushPromises()
    const revealedInput = wrapper.findAll('input').find(i => i.element.value === 'OLD-KEY')
    await revealedInput.setValue('NEW-KEY-TYPED-BY-USER')

    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER] }) // current-list re-fetch in saveServers
    api.put.mockResolvedValueOnce({ data: {} })
    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER] }) // reload after save
    const saveBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.servers.save')))
    await saveBtn.trigger('click')
    await flushPromises()
    expect(api.put).toHaveBeenCalledWith('/settings/media-servers/5', expect.objectContaining({ api_key: 'NEW-KEY-TYPED-BY-USER' }))
  })

  it('strips all internal UI-only fields (_uid, _showKey, _testing, etc.) from the save payload', async () => {
    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER] })
    api.post.mockResolvedValue({ data: { ok: true } })
    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER] })
    api.put.mockResolvedValueOnce({ data: {} })
    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER] })
    const wrapper = mountTab()
    await flushPromises()
    const saveBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.servers.save')))
    await saveBtn.trigger('click')
    await flushPromises()
    const sentPayload = api.put.mock.calls[0][1]
    for (const key of ['_uid', '_showKey', '_testing', '_testOk', '_testMsg', '_purging', '_purged']) {
      expect(sentPayload).not.toHaveProperty(key)
    }
  })

  it('deletes servers that were removed from the list (present in the old list, absent from the new one)', async () => {
    const otherServer = { ...MASKED_SERVER, id: '6', name: 'Autre' }
    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER, otherServer] }) // initial load, 2 servers
    api.post.mockResolvedValue({ data: { ok: true } })
    const wrapper = mountTab()
    await flushPromises()
    // Remove the second server from the UI list before saving
    const trashButtons = wrapper.findAll('button').filter(b => b.find('.fa-trash').exists() && !b.find('.fa-trash-can').exists())
    await trashButtons[1].trigger('click')

    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER, otherServer] }) // current-list re-fetch
    api.put.mockResolvedValueOnce({ data: {} })
    api.delete.mockResolvedValueOnce({ data: {} })
    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER] })
    const saveBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.servers.save')))
    await saveBtn.trigger('click')
    await flushPromises()
    expect(api.delete).toHaveBeenCalledWith('/settings/media-servers/6')
  })

  it('addServer appends a blank server entry to the list', async () => {
    api.get.mockResolvedValueOnce({ data: [] })
    const wrapper = mountTab()
    await flushPromises()
    expect(wrapper.text()).toContain(i18n.global.t('settings.servers.empty'))
    const addBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.servers.add')))
    await addBtn.trigger('click')
    expect(wrapper.find('input[placeholder="Mon serveur"]').exists()).toBe(true)
  })

  it('testServer shows the ok state and updates the server type from the API response', async () => {
    api.get.mockResolvedValueOnce({ data: [{ ...MASKED_SERVER, type: '' }] })
    api.post.mockResolvedValue({ data: { ok: true } }) // silentTest first pass (type '' -> not enough info, url present though)
    const wrapper = mountTab()
    await flushPromises()
    api.post.mockResolvedValueOnce({ data: { ok: true, server_type: 'jellyfin' } })
    const testBtn = wrapper.findAll('button').find(b => /^(Tester|✓ OK|…)$/.test(b.text()))
    await testBtn.trigger('click')
    await flushPromises()
    expect(checkServerHealth).toHaveBeenCalled()
    expect(wrapper.text()).toContain('jellyfin')
  })

  it('testServer prefers the translated error_code message over the raw API message when the key exists', async () => {
    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER] })
    api.post.mockResolvedValue({ data: { ok: true } })
    const wrapper = mountTab()
    await flushPromises()
    api.post.mockResolvedValueOnce({ data: { ok: false, error_code: 'http_401', message: 'raw upstream text' } })
    const testBtn = wrapper.findAll('button').find(b => /^(Tester|✓ OK|…)$/.test(b.text()))
    await testBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Clé API invalide')
    expect(wrapper.text()).not.toContain('raw upstream text')
  })

  it('testServer falls back to the raw API message when error_code has no translation key', async () => {
    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER] })
    api.post.mockResolvedValue({ data: { ok: true } })
    const wrapper = mountTab()
    await flushPromises()
    api.post.mockResolvedValueOnce({ data: { ok: false, error_code: 'not_a_real_key', message: 'raw upstream text' } })
    const testBtn = wrapper.findAll('button').find(b => /^(Tester|✓ OK|…)$/.test(b.text()))
    await testBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('raw upstream text')
  })

  it('purgeServerQueue shows the purged count and auto-clears it after 4s', async () => {
    vi.useFakeTimers()
    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER] })
    api.post.mockResolvedValue({ data: { ok: true } })
    const wrapper = mountTab()
    await flushPromises()
    api.post.mockResolvedValueOnce({ data: { purged: 12 } })
    const purgeBtn = wrapper.findAll('button').find(b => b.find('.fa-trash-can').exists())
    await purgeBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('✓ 12')
    await vi.advanceTimersByTimeAsync(4000)
    expect(wrapper.text()).not.toContain('✓ 12')
    vi.useRealTimers()
  })

  it('removeServer removes the given entry from the local list without calling the API', async () => {
    api.get.mockResolvedValueOnce({ data: [] })
    const wrapper = mountTab()
    await flushPromises()
    const addBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.servers.add')))
    await addBtn.trigger('click')
    await addBtn.trigger('click')
    expect(wrapper.findAll('input[placeholder="Mon serveur"]')).toHaveLength(2)
    const trashButtons = wrapper.findAll('button').filter(b => b.find('.fa-trash').exists() && !b.find('.fa-trash-can').exists())
    await trashButtons[0].trigger('click')
    expect(wrapper.findAll('input[placeholder="Mon serveur"]')).toHaveLength(1)
    expect(api.delete).not.toHaveBeenCalled()
  })

  it('editing name/url/ext_url fields updates the model and is reflected in the save payload', async () => {
    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER] })
    api.post.mockResolvedValue({ data: { ok: true } })
    const wrapper = mountTab()
    await flushPromises()
    const urlInputs = wrapper.findAll('input[type=url]')
    await wrapper.find('input[placeholder="Mon serveur"]').setValue('Nouveau nom')
    await urlInputs[0].setValue('http://emby2:8096')   // local URL
    await urlInputs[1].setValue('https://emby.example.com') // external URL

    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER] })
    api.put.mockResolvedValueOnce({ data: {} })
    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER] })
    const saveBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.servers.save')))
    await saveBtn.trigger('click')
    await flushPromises()
    const sentPayload = api.put.mock.calls[0][1]
    expect(sentPayload.name).toBe('Nouveau nom')
    expect(sentPayload.url).toBe('http://emby2:8096')
    expect(sentPayload.ext_url).toBe('https://emby.example.com')
  })

  it('changing the type select to plex updates the external-URL placeholder accordingly', async () => {
    api.get.mockResolvedValueOnce({ data: [{ ...MASKED_SERVER, id: '' }] }) // unsaved (no id) so no background test call
    const wrapper = mountTab()
    await flushPromises()
    expect(wrapper.findAll('input[type=url]')[1].attributes('placeholder')).toContain('emby.mondomaine.fr')
    await wrapper.find('select').setValue('plex')
    await flushPromises()
    expect(wrapper.findAll('input[type=url]')[1].attributes('placeholder')).toContain('plex.mondomaine.fr')
  })

  it('shows the Plex webhook secret + overlay section only for a Plex server, and computes the webhook URL with/without a secret', async () => {
    api.get.mockResolvedValueOnce({ data: [{ ...MASKED_SERVER, type: 'plex' }] })
    api.post.mockResolvedValue({ data: { ok: true } })
    const wrapper = mountTab({ plex_webhook_secret: '' })
    await flushPromises()
    expect(wrapper.text()).toContain(i18n.global.t('settings.servers.plexSettings'))
    expect(wrapper.text()).toContain('/api/plex/webhook')
    expect(wrapper.text()).not.toContain('secret=')
  })

  it('appends ?secret= to the webhook URL once plex_webhook_secret is set', async () => {
    api.get.mockResolvedValueOnce({ data: [{ ...MASKED_SERVER, type: 'plex' }] })
    api.post.mockResolvedValue({ data: { ok: true } })
    const wrapper = mountTab({ plex_webhook_secret: 'sekret123' })
    await flushPromises()
    expect(wrapper.text()).toContain('secret=sekret123')
  })

  it('toggling the webhook-secret eye icon reveals it as plain text', async () => {
    api.get.mockResolvedValueOnce({ data: [{ ...MASKED_SERVER, type: 'plex' }] })
    api.post.mockResolvedValue({ data: { ok: true } })
    const wrapper = mountTab({ plex_webhook_secret: 'sekret123' })
    await flushPromises()
    const secretInput = wrapper.findAll('input').find(i => i.element.value === 'sekret123')
    expect(secretInput.attributes('type')).toBe('password')
    const eyeBtns = wrapper.findAll('button').filter(b => b.find('.fa-eye').exists())
    await eyeBtns[eyeBtns.length - 1].trigger('click') // webhook secret's own eye toggle
    expect(secretInput.attributes('type')).toBe('text')
  })

  it('discoverPlex lists Plex sections and lets the user add an unconfigured one', async () => {
    api.get.mockResolvedValueOnce({ data: [{ ...MASKED_SERVER, type: 'plex' }] })
    api.post.mockResolvedValue({ data: { ok: true } })
    const wrapper = mountTab()
    await flushPromises()
    api.get.mockResolvedValueOnce({
      data: [{ id: 's1', title: 'Films', type: 'movie', configured: false }],
    })
    const discoverBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('common.discover')))
    await discoverBtn.trigger('click')
    await flushPromises()
    expect(api.get).toHaveBeenCalledWith('/libraries/plex/5/sections')
    expect(wrapper.text()).toContain('Films')

    api.post.mockResolvedValueOnce({ data: {} }) // POST /libraries
    const addSectionBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.servers.addSection')))
    await addSectionBtn.trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith('/libraries', expect.objectContaining({ emby_library_id: 's1', server_id: '5' }))
    expect(serversFetch).toHaveBeenCalled()
  })

  it('discoverPlex shows an error message when the sections request fails', async () => {
    api.get.mockResolvedValueOnce({ data: [{ ...MASKED_SERVER, type: 'plex' }] })
    api.post.mockResolvedValue({ data: { ok: true } })
    const wrapper = mountTab()
    await flushPromises()
    api.get.mockRejectedValueOnce({ response: { data: { detail: 'Section indisponible' } } })
    const discoverBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('common.discover')))
    await discoverBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Section indisponible')
  })

  it('shows the emby "leaving soon" collection fields for an emby/jellyfin server and lets the user edit them', async () => {
    api.get.mockResolvedValueOnce({ data: [MASKED_SERVER] })
    api.post.mockResolvedValue({ data: { ok: true } })
    const wrapper = mountTab()
    await flushPromises()
    expect(wrapper.text()).toContain(i18n.global.t('settings.servers.leavingSoonCollection'))
    const collectionInput = wrapper.find(`input[placeholder="${i18n.global.t('settings.servers.leavingSoonDefault')}"]`)
    await collectionInput.setValue('Ma collection')
    const daysInput = wrapper.find('input[type=number]')
    await daysInput.setValue('5')
    expect(daysInput.element.value).toBe('5')
  })

  it('scheduleAutoDetect re-tests the server 800ms after its URL changes, and updates the type on a positive detection', async () => {
    vi.useFakeTimers()
    api.get.mockResolvedValueOnce({ data: [{ ...MASKED_SERVER, type: '' }] })
    // Keyed on the URL, not on call order: any other POST (e.g. an initial
    // connection test) must not consume the detection response.
    let detected = false
    api.post.mockImplementation(url => Promise.resolve(
      url === '/settings/media-servers/5/test' && detected
        ? { data: { ok: true, server_type: 'jellyfin' } }
        : { data: { ok: true } },
    ))
    const wrapper = mountTab()
    await flushPromises()
    const typeSelect = wrapper.findAll('select').find(sel => sel.findAll('option').some(o => o.element.value === 'jellyfin'))
    expect(typeSelect.element.value).not.toBe('jellyfin')
    detected = true
    api.post.mockClear()
    await wrapper.find('input[placeholder="http://192.168.1.10:8096"]').setValue('http://newhost:8096')
    await vi.advanceTimersByTimeAsync(799)
    expect(api.post).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith('/settings/media-servers/5/test')
    expect(typeSelect.element.value).toBe('jellyfin')
    vi.useRealTimers()
  })

  it('unmounting the component clears any pending auto-detect timer (no stray API call after unmount)', async () => {
    vi.useFakeTimers()
    api.get.mockResolvedValueOnce({ data: [{ ...MASKED_SERVER, type: '' }] })
    api.post.mockResolvedValue({ data: { ok: true } })
    const wrapper = mountTab()
    await flushPromises()
    await wrapper.find('input[placeholder="http://192.168.1.10:8096"]').setValue('http://newhost:8096')
    wrapper.unmount()
    api.post.mockClear()
    await vi.advanceTimersByTimeAsync(800)
    await flushPromises()
    expect(api.post).not.toHaveBeenCalled()
    vi.useRealTimers()
  })
})
