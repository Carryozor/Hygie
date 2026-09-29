// src/components/settings/__tests__/DiscordTab.cov.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { i18n } from '@/i18n'

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
import api from '@/api/client'
import DiscordTab from '../DiscordTab.vue'

function baseForm(overrides = {}) {
  return {
    discord_webhook: '', discord_notif_thresholds: '',
    discord_webhook_alerts: '',
    discord_alert_deletion_error: 'false', discord_alert_deletion_error_mention: '', discord_alert_deletion_error_msg: '',
    discord_alert_scan_failure: 'false', discord_alert_scan_failure_mention: '', discord_alert_scan_failure_msg: '',
    discord_alert_seerr_failure: 'false', discord_alert_seerr_failure_mention: '', discord_alert_seerr_failure_msg: '',
    discord_alert_error_threshold: 5,
    ...overrides,
  }
}

function mountTab(formOverrides = {}) {
  return mount(DiscordTab, { props: { form: baseForm(formOverrides) }, global: { plugins: [i18n] } })
}

describe('DiscordTab', () => {
  beforeEach(() => vi.clearAllMocks())

  it('reveals the masked main webhook via GET /settings/reveal/discord_webhook', async () => {
    api.get.mockResolvedValueOnce({ data: { value: 'https://discord.com/api/webhooks/real' } })
    const wrapper = mountTab({ discord_webhook: '***' })
    const eyeBtns = wrapper.findAll('button').filter(b => b.find('.fa-eye').exists())
    await eyeBtns[0].trigger('click')
    await flushPromises()
    expect(api.get).toHaveBeenCalledWith('/settings/reveal/discord_webhook')
    const revealed = wrapper.findAll('input').find(i => i.element.value === 'https://discord.com/api/webhooks/real')
    expect(revealed.attributes('type')).toBe('text')
  })

  it('reveals the masked alerts webhook via GET /settings/reveal/discord_webhook_alerts, independently of the main one', async () => {
    api.get.mockResolvedValueOnce({ data: { value: 'https://discord.com/api/webhooks/alerts-real' } })
    const wrapper = mountTab({ discord_webhook: 'already-real', discord_webhook_alerts: '***' })
    const eyeBtns = wrapper.findAll('button').filter(b => b.find('.fa-eye').exists())
    await eyeBtns[1].trigger('click')
    await flushPromises()
    expect(api.get).toHaveBeenCalledWith('/settings/reveal/discord_webhook_alerts')
    const revealed = wrapper.findAll('input').find(i => i.element.value === 'https://discord.com/api/webhooks/alerts-real')
    expect(revealed).toBeTruthy()
  })

  it('does not call reveal for a webhook that is already a real (unmasked) value', async () => {
    const wrapper = mountTab({ discord_webhook: 'https://discord.com/api/webhooks/real' })
    const eyeBtns = wrapper.findAll('button').filter(b => b.find('.fa-eye').exists())
    await eyeBtns[0].trigger('click')
    await flushPromises()
    expect(api.get).not.toHaveBeenCalled()
  })

  it('renders three AlertRow instances (deletion error, scan failure, seerr failure)', () => {
    const wrapper = mountTab()
    expect(wrapper.findAll('input[type=checkbox]')).toHaveLength(3)
  })

  it('toggling the deletion-error AlertRow updates form.discord_alert_deletion_error as a string', async () => {
    const form = baseForm()
    const wrapper = mount(DiscordTab, { props: { form }, global: { plugins: [i18n] } })
    const toggles = wrapper.findAll('input[type=checkbox]')
    await toggles[0].setValue(true)
    expect(form.discord_alert_deletion_error).toBe('true')
  })

  it('typing a mention into the scan-failure AlertRow updates form.discord_alert_scan_failure_mention', async () => {
    const form = baseForm({ discord_alert_scan_failure: 'true' })
    const wrapper = mount(DiscordTab, { props: { form }, global: { plugins: [i18n] } })
    const toggles = wrapper.findAll('input[type=checkbox]')
    expect(toggles[1].element.checked).toBe(true)
    // input[0] is the always-present "notification thresholds" field; input[1]/[2]
    // are the scan-failure AlertRow's mention/message (only rendered since it's enabled).
    const mentionInputs = wrapper.findAll('input[type=text]')
    await mentionInputs[1].setValue('@team')
    expect(form.discord_alert_scan_failure_mention).toBe('@team')
  })

  it('toggling the deletion-error AlertRow message field updates form.discord_alert_deletion_error_msg', async () => {
    const form = baseForm({ discord_alert_deletion_error: 'true' })
    const wrapper = mount(DiscordTab, { props: { form }, global: { plugins: [i18n] } })
    const msgInputs = wrapper.findAll('input[type=text]')
    await msgInputs[2].setValue('Suppression en erreur') // [0]=thresholds, [1]=mention, [2]=msg
    expect(form.discord_alert_deletion_error_msg).toBe('Suppression en erreur')
  })

  it('toggling the seerr-failure AlertRow mention field updates form.discord_alert_seerr_failure_mention', async () => {
    const form = baseForm({ discord_alert_seerr_failure: 'true' })
    const wrapper = mount(DiscordTab, { props: { form }, global: { plugins: [i18n] } })
    const inputs = wrapper.findAll('input[type=text]')
    await inputs[1].setValue('@seerr-admins')
    expect(form.discord_alert_seerr_failure_mention).toBe('@seerr-admins')
  })

  it('editing the error threshold number field coerces to a Number on form', async () => {
    const form = baseForm()
    const wrapper = mount(DiscordTab, { props: { form }, global: { plugins: [i18n] } })
    await wrapper.find('input[type=number]').setValue('10')
    expect(form.discord_alert_error_threshold).toBe(10)
  })

  it('editing the notification thresholds text field mutates the form prop', async () => {
    const form = baseForm()
    const wrapper = mount(DiscordTab, { props: { form }, global: { plugins: [i18n] } })
    // The first plain text input (outside AlertRow) is the thresholds field
    const thresholdsInput = wrapper.find('input[type=text]')
    await thresholdsInput.setValue('1,3,7')
    expect(form.discord_notif_thresholds).toBe('1,3,7')
  })
})
