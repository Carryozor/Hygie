// src/components/settings/__tests__/SecurityTab.cov.test.js
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { i18n } from '@/i18n'

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
import api from '@/api/client'
import { getToken, clearToken } from '@/api/tokenStore'
import SecurityTab from '../SecurityTab.vue'

const OFF = { enabled: false, enabled_at: null, pending: false, backup_codes_remaining: 0, encryption_available: true }
const ON = { enabled: true, enabled_at: '2026-11-24T10:00:00+00:00', pending: false, backup_codes_remaining: 9, encryption_available: true }
const QR = 'data:image/svg+xml;base64,AAAA'
const CODES = Array.from({ length: 10 }, (_, i) => `abcdefghijklmn${String(i).padStart(2, '2')}`)

async function mountTab(status = OFF) {
  const pinia = createPinia()
  setActivePinia(pinia)
  api.get.mockResolvedValue({ data: status })
  const wrapper = mount(SecurityTab, { global: { plugins: [pinia, i18n] } })
  await flushPromises()
  return wrapper
}
const $ = (w, name) => w.find(`[data-test="${name}"]`)

// Drives the wizard up to the "backup codes shown" screen.
async function reachCodes(wrapper) {
  await $(wrapper, 'setup-password').setValue('pw')
  api.post.mockResolvedValueOnce({ data: { secret: 'JBSWY3DPEHPK3PXP', otpauth_uri: 'otpauth://totp/x', qr_svg_data_uri: QR } })
  await $(wrapper, 'setup-start').trigger('click')
  await flushPromises()
  await $(wrapper, 'enable-code').setValue('123456')
  api.post.mockResolvedValueOnce({ data: { enabled: true, backup_codes: CODES, access_token: 'newtok' } })
  await $(wrapper, 'enable-confirm').trigger('click')
  await flushPromises()
}

describe('SecurityTab', () => {
  beforeEach(() => { vi.resetAllMocks(); clearToken() })
  afterEach(() => vi.unstubAllGlobals())

  it('loads the MFA status from GET /auth/mfa', async () => {
    await mountTab()
    expect(api.get).toHaveBeenCalledWith('/auth/mfa')
  })

  it('full setup flow, codes gone after acknowledge', async () => {
    const wrapper = await mountTab()
    await $(wrapper, 'setup-password').setValue('pw')
    api.post.mockResolvedValueOnce({ data: { secret: 'JBSWY3DPEHPK3PXP', otpauth_uri: 'otpauth://totp/x', qr_svg_data_uri: QR } })
    await $(wrapper, 'setup-start').trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith('/auth/mfa/setup', { current_password: 'pw' })
    // QR rendered as an <img>, never injected as HTML
    expect(wrapper.find('img[data-test="qr"]').attributes('src')).toBe(QR)
    expect(wrapper.text()).toContain('JBSWY3DPEHPK3PXP')

    await $(wrapper, 'enable-code').setValue('123456')
    api.post.mockResolvedValueOnce({ data: { enabled: true, backup_codes: CODES, access_token: 'newtok' } })
    api.get.mockResolvedValue({ data: ON })
    await $(wrapper, 'enable-confirm').trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenLastCalledWith('/auth/mfa/enable', { code: '123456' })
    for (const c of CODES) expect(wrapper.text()).toContain(c)
    // The secret/QR step is gone and the done button waits for the checkbox
    expect(wrapper.find('img[data-test="qr"]').exists()).toBe(false)
    expect($(wrapper, 'codes-done').attributes('disabled')).toBeDefined()

    await $(wrapper, 'codes-ack').setValue(true)
    expect($(wrapper, 'codes-done').attributes('disabled')).toBeUndefined()
    await $(wrapper, 'codes-done').trigger('click')
    await flushPromises()
    for (const c of CODES) expect(wrapper.text()).not.toContain(c)
    expect($(wrapper, 'disable-confirm').exists()).toBe(true)
  })

  it('adopts returned access_token', async () => {
    const wrapper = await mountTab()
    await reachCodes(wrapper)
    expect(getToken()).toBe('newtok')
  })

  it('disable requires both fields', async () => {
    const wrapper = await mountTab(ON)
    const btn = () => $(wrapper, 'disable-confirm')
    expect(btn().attributes('disabled')).toBeDefined()
    await $(wrapper, 'disable-password').setValue('pw')
    expect(btn().attributes('disabled')).toBeDefined()
    await $(wrapper, 'disable-code').setValue('123456')
    expect(btn().attributes('disabled')).toBeUndefined()

    api.post.mockResolvedValueOnce({ data: { enabled: false, access_token: 'tok-after-disable' } })
    api.get.mockResolvedValue({ data: OFF })
    await btn().trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith('/auth/mfa/disable', { current_password: 'pw', code: '123456' })
    expect(getToken()).toBe('tok-after-disable')
    expect($(wrapper, 'setup-start').exists()).toBe(true)
  })

  it('encryption unavailable disables setup', async () => {
    const wrapper = await mountTab({ ...OFF, encryption_available: false })
    expect($(wrapper, 'encryption-warning').exists()).toBe(true)
    expect($(wrapper, 'setup-start').attributes('disabled')).toBeDefined()
    await $(wrapper, 'setup-start').trigger('click')
    expect(api.post).not.toHaveBeenCalled()
  })

  it('shows the server detail when the password is rejected', async () => {
    const wrapper = await mountTab()
    await $(wrapper, 'setup-password').setValue('bad')
    api.post.mockRejectedValueOnce({ response: { status: 400, data: { detail: 'Mot de passe incorrect', code: 'invalid_password' } } })
    await $(wrapper, 'setup-start').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Mot de passe incorrect')
    expect(wrapper.find('img[data-test="qr"]').exists()).toBe(false)
  })

  it('falls back to a generic error without a server detail', async () => {
    const wrapper = await mountTab()
    await $(wrapper, 'setup-password').setValue('pw')
    api.post.mockRejectedValueOnce(new Error('network'))
    await $(wrapper, 'setup-start').trigger('click')
    await flushPromises()
    expect($(wrapper, 'error').exists()).toBe(true)
  })

  it('refreshes the status when the server says MFA is already enabled', async () => {
    const wrapper = await mountTab()
    await $(wrapper, 'setup-password').setValue('pw')
    api.post.mockRejectedValueOnce({ response: { status: 409, data: { detail: 'déjà actif', code: 'mfa_already_enabled' } } })
    api.get.mockResolvedValue({ data: ON })
    await $(wrapper, 'setup-start').trigger('click')
    await flushPromises()
    expect($(wrapper, 'disable-confirm').exists()).toBe(true)
  })

  it('copies the backup codes and downloads them as a local .txt Blob', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    const createObjectURL = vi.fn(() => 'blob:local')
    const revokeObjectURL = vi.fn()
    URL.createObjectURL = createObjectURL
    URL.revokeObjectURL = revokeObjectURL

    const wrapper = await mountTab()
    await reachCodes(wrapper)

    await $(wrapper, 'codes-copy').trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenCalledWith(CODES.join('\n'))

    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    await $(wrapper, 'codes-download').trigger('click')
    expect(createObjectURL).toHaveBeenCalledTimes(1)
    const blob = createObjectURL.mock.calls[0][0]
    expect(blob).toBeInstanceOf(Blob)
    expect(blob.size).toBeGreaterThan(CODES.join('\n').length - 1)
    expect(clickSpy).toHaveBeenCalled()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:local')
    clickSpy.mockRestore()
  })

  it('does not crash when the clipboard is unavailable', async () => {
    vi.stubGlobal('navigator', { ...navigator, clipboard: undefined })
    const wrapper = await mountTab()
    await reachCodes(wrapper)
    await $(wrapper, 'codes-copy').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain(CODES[0])
  })

  it('survives a failed status fetch', async () => {
    api.get.mockRejectedValue(new Error('boom'))
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(SecurityTab, { global: { plugins: [pinia, i18n] } })
    await flushPromises()
    expect($(wrapper, 'error').exists()).toBe(true)
  })

  it('cancels a pending setup and returns to the password step', async () => {
    const wrapper = await mountTab()
    await $(wrapper, 'setup-password').setValue('pw')
    api.post.mockResolvedValueOnce({ data: { secret: 'S', otpauth_uri: 'o', qr_svg_data_uri: QR } })
    await $(wrapper, 'setup-start').trigger('click')
    await flushPromises()
    await $(wrapper, 'setup-cancel').trigger('click')
    expect(wrapper.find('img[data-test="qr"]').exists()).toBe(false)
    expect($(wrapper, 'setup-start').exists()).toBe(true)
  })
})
