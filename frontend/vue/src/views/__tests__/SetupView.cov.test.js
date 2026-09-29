// src/views/__tests__/SetupView.cov.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mountView, flushAll } from './testUtils.cov.js'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

import api from '@/api/client'
import SetupView from '../SetupView.vue'

describe('SetupView', () => {
  beforeEach(() => vi.clearAllMocks())

  it('submits username/password to auth.setup and redirects to "/" on success', async () => {
    api.post.mockResolvedValueOnce({ data: { access_token: 'tok', username: 'admin' } })
    const { wrapper, router } = await mountView(SetupView, { path: '/setup' })
    const pushSpy = vi.spyOn(router, 'push')

    await wrapper.find('input[type="text"]').setValue('admin')
    await wrapper.find('input[type="password"]').setValue('secret')
    await wrapper.find('form').trigger('submit.prevent')
    await flushAll()

    expect(api.post).toHaveBeenCalledWith('/auth/setup', { username: 'admin', password: 'secret' })
    expect(pushSpy).toHaveBeenCalledWith('/')
  })

  it('shows the backend detail message on a validation failure and does not navigate', async () => {
    api.post.mockRejectedValueOnce({ response: { status: 422, data: { detail: 'Mot de passe trop court' } } })
    const { wrapper, router } = await mountView(SetupView, { path: '/setup' })
    const pushSpy = vi.spyOn(router, 'push')

    await wrapper.find('input[type="text"]').setValue('admin')
    await wrapper.find('input[type="password"]').setValue('a')
    await wrapper.find('form').trigger('submit.prevent')
    await flushAll()

    expect(wrapper.text()).toContain('Mot de passe trop court')
    expect(pushSpy).not.toHaveBeenCalled()
  })

  it('falls back to the generic setup error message when the backend gives no detail', async () => {
    api.post.mockRejectedValueOnce({ response: { status: 500, data: {} } })
    const { wrapper } = await mountView(SetupView, { path: '/setup' })

    await wrapper.find('input[type="text"]').setValue('admin')
    await wrapper.find('input[type="password"]').setValue('secret')
    await wrapper.find('form').trigger('submit.prevent')
    await flushAll()

    expect(wrapper.text()).toContain('Erreur lors de la création du compte')
  })

  it('disables the submit button and shows the creating label while in flight', async () => {
    let resolveSetup
    api.post.mockImplementationOnce(() => new Promise(r => { resolveSetup = r }))
    const { wrapper } = await mountView(SetupView, { path: '/setup' })

    await wrapper.find('input[type="text"]').setValue('admin')
    await wrapper.find('input[type="password"]').setValue('secret')
    await wrapper.find('form').trigger('submit.prevent')
    await flushAll()

    const btn = wrapper.find('button[type="submit"]')
    expect(btn.attributes('disabled')).toBeDefined()
    expect(btn.text()).toBe('Création...')

    resolveSetup({ data: { access_token: 'tok' } })
    await flushAll()
  })
})
