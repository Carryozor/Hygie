// src/components/settings/__tests__/SeerrTab.cov.test.js
//
// SeerrTab's api key is masked ('***') and only revealed via
// useRevealSetting -> GET /settings/reveal/seerr_api_key (see
// src/composables/useRevealSetting.js). We mock only the transport
// (@/api/client) and let the real composable run, so this test exercises
// the actual masking contract end-to-end at the front boundary.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { i18n } from '@/i18n'

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
import api from '@/api/client'
import SeerrTab from '../SeerrTab.vue'

function mountTab(formOverrides = {}) {
  return mount(SeerrTab, {
    props: { form: { seerr_url: '', seerr_api_key: '', seerr_external_url: '', ...formOverrides } },
    global: { plugins: [i18n] },
  })
}

describe('SeerrTab', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders the masked api key as a password field without revealing it eagerly', () => {
    const wrapper = mountTab({ seerr_api_key: '***' })
    const keyInput = wrapper.find('input[type=password]')
    expect(keyInput.element.value).toBe('***')
    expect(api.get).not.toHaveBeenCalled()
  })

  it('clicking the eye reveals the real key via GET /settings/reveal/seerr_api_key', async () => {
    api.get.mockResolvedValueOnce({ data: { value: 'sk-real-key' } })
    const wrapper = mountTab({ seerr_api_key: '***' })
    const eyeBtn = wrapper.findAll('button').find(b => b.find('.fa-eye').exists())
    await eyeBtn.trigger('click')
    await flushPromises()
    expect(api.get).toHaveBeenCalledWith('/settings/reveal/seerr_api_key')
    const revealedInput = wrapper.findAll('input').find(i => i.element.value === 'sk-real-key')
    expect(revealedInput.attributes('type')).toBe('text')
  })

  it('does not call reveal when the key is not masked (already a real value)', async () => {
    const wrapper = mountTab({ seerr_api_key: 'already-real' })
    const eyeBtn = wrapper.findAll('button').find(b => b.find('.fa-eye').exists())
    await eyeBtn.trigger('click')
    await flushPromises()
    expect(api.get).not.toHaveBeenCalled()
  })

  it('the sync button is disabled until both seerr_url and seerr_api_key are present', () => {
    const wrapper = mountTab({ seerr_url: '', seerr_api_key: '' })
    const syncBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.seerr.import')))
    expect(syncBtn.attributes('disabled')).toBeDefined()
  })

  it('syncFromSeerr posts seerr_url/seerr_api_key and stores the returned arr servers as JSON', async () => {
    const wrapper = mountTab({ seerr_url: 'https://seerr.local', seerr_api_key: 'key123' })
    api.post.mockResolvedValueOnce({
      data: { message: '2 imported', radarr_servers: [{ id: 1 }], sonarr_servers: [{ id: 2 }] },
    })
    const syncBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.seerr.import')))
    await syncBtn.trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith('/settings/sync-arr-from-seerr', {
      seerr_url: 'https://seerr.local', seerr_api_key: 'key123',
    })
    expect(wrapper.text()).toContain('2 imported')
  })

  it('syncFromSeerr shows the server error detail and does not crash on failure', async () => {
    const wrapper = mountTab({ seerr_url: 'https://seerr.local', seerr_api_key: 'key123' })
    api.post.mockRejectedValueOnce({ response: { data: { detail: 'Unauthorized' } } })
    const syncBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.seerr.import')))
    await syncBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Unauthorized')
  })

  it('loadUsers lists seerr users with an empty discord-id field ready for editing', async () => {
    api.get.mockResolvedValueOnce({ data: [{ id: 5, username: 'zoe', discord_id: '' }] })
    const wrapper = mountTab()
    const loadBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.seerr.loadUsers')))
    await loadBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('zoe')
  })

  it('shows an error and marks fetched=true when loadUsers fails (no infinite spinner)', async () => {
    api.get.mockRejectedValueOnce({ response: { data: { detail: 'Seerr unreachable' } } })
    const wrapper = mountTab()
    const loadBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.seerr.loadUsers')))
    await loadBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Seerr unreachable')
    expect(wrapper.find('.fa-spinner').exists()).toBe(false)
  })

  it('saveDiscordId posts the trimmed discord id and shows the saved checkmark', async () => {
    api.get.mockResolvedValueOnce({ data: [{ id: 5, username: 'zoe', discord_id: '' }] })
    const wrapper = mountTab()
    const loadBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.seerr.loadUsers')))
    await loadBtn.trigger('click')
    await flushPromises()
    await wrapper.find('input[type=text]').setValue('  123456  ')
    api.post.mockResolvedValueOnce({ data: {} })
    const saveBtn = wrapper.findAll('button').find(b => b.find('.fa-save').exists())
    await saveBtn.trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith('/seerr-rules/discord-mappings', {
      seerr_user_id: 5, seerr_username: 'zoe', discord_id: '123456',
    })
    expect(wrapper.find('.fa-check').exists()).toBe(true)
  })
})
