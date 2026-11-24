// src/views/__tests__/LoginView.cov.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mountView, flushAll } from './testUtils.cov.js'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

import api from '@/api/client'
import LoginView from '../LoginView.vue'

describe('LoginView', () => {
  beforeEach(() => vi.clearAllMocks())

  it('submits username/password to auth.login and redirects to "/" by default', async () => {
    api.post.mockResolvedValueOnce({ data: { access_token: 'tok', username: 'admin' } })
    const { wrapper, router } = await mountView(LoginView, { path: '/login' })
    const pushSpy = vi.spyOn(router, 'push')

    await wrapper.find('input[type="text"]').setValue('admin')
    await wrapper.find('input[type="password"]').setValue('secret')
    await wrapper.find('form').trigger('submit.prevent')
    await flushAll()

    expect(api.post).toHaveBeenCalledWith('/auth/login', { username: 'admin', password: 'secret' })
    expect(pushSpy).toHaveBeenCalledWith('/')
  })

  it('redirects to the ?redirect= query target after a successful login', async () => {
    api.post.mockResolvedValueOnce({ data: { access_token: 'tok', username: 'admin' } })
    const { wrapper, router } = await mountView(LoginView, { path: '/login?redirect=%2Fqueue' })
    const pushSpy = vi.spyOn(router, 'push')

    await wrapper.find('input[type="text"]').setValue('admin')
    await wrapper.find('input[type="password"]').setValue('secret')
    await wrapper.find('form').trigger('submit.prevent')
    await flushAll()

    expect(pushSpy).toHaveBeenCalledWith('/queue')
  })

  it('shows an error message and does not navigate when login rejects', async () => {
    api.post.mockRejectedValueOnce({ response: { status: 401, data: {} } })
    const { wrapper, router } = await mountView(LoginView, { path: '/login' })
    const pushSpy = vi.spyOn(router, 'push')

    await wrapper.find('input[type="text"]').setValue('admin')
    await wrapper.find('input[type="password"]').setValue('wrong')
    await wrapper.find('form').trigger('submit.prevent')
    await flushAll()

    expect(wrapper.text()).toContain('Identifiants incorrects')
    expect(pushSpy).not.toHaveBeenCalled()
  })

  it('disables the submit button and shows the loading label while the request is in flight', async () => {
    let resolveLogin
    api.post.mockImplementationOnce(() => new Promise(r => { resolveLogin = r }))
    const { wrapper } = await mountView(LoginView, { path: '/login' })

    await wrapper.find('input[type="text"]').setValue('admin')
    await wrapper.find('input[type="password"]').setValue('secret')
    await wrapper.find('form').trigger('submit.prevent')
    await flushAll()

    const btn = wrapper.find('button[type="submit"]')
    expect(btn.attributes('disabled')).toBeDefined()
    expect(btn.text()).toBe('Connexion...')

    resolveLogin({ data: { access_token: 'tok' } })
    await flushAll()
    expect(wrapper.find('button[type="submit"]').attributes('disabled')).toBeUndefined()
  })

  it('clears a previous error message on a subsequent submit attempt', async () => {
    api.post.mockRejectedValueOnce({ response: { status: 401, data: {} } })
    const { wrapper } = await mountView(LoginView, { path: '/login' })
    await wrapper.find('input[type="text"]').setValue('admin')
    await wrapper.find('input[type="password"]').setValue('wrong')
    await wrapper.find('form').trigger('submit.prevent')
    await flushAll()
    expect(wrapper.text()).toContain('Identifiants incorrects')

    api.post.mockResolvedValueOnce({ data: { access_token: 'tok' } })
    await wrapper.find('form').trigger('submit.prevent')
    await flushAll()
    expect(wrapper.text()).not.toContain('Identifiants incorrects')
  })
  describe('MFA second step', () => {
    const MFA = { data: { mfa_required: true, mfa_token: 't'.repeat(43), expires_in: 300 } }

    async function toStep2(path = '/login') {
      api.post.mockResolvedValueOnce(MFA)
      const env = await mountView(LoginView, { path })
      env.pushSpy = vi.spyOn(env.router, 'push')
      await env.wrapper.find('input[type="text"]').setValue('admin')
      await env.wrapper.find('input[type="password"]').setValue('secret')
      await env.wrapper.find('form').trigger('submit.prevent')
      await flushAll()
      return env
    }

    it('shows code step and does not navigate', async () => {
      const { wrapper, pushSpy } = await toStep2()
      expect(wrapper.find('[data-test="mfa-code"]').exists()).toBe(true)
      expect(wrapper.find('[data-test="mfa-code"]').attributes('autocomplete')).toBe('one-time-code')
      expect(wrapper.find('[data-test="mfa-code"]').attributes('inputmode')).toBe('numeric')
      expect(wrapper.find('input[type="password"]').exists()).toBe(false)
      expect(pushSpy).not.toHaveBeenCalled()
    })

    it('posts {mfa_token, code} to /auth/login/mfa then navigates', async () => {
      const { wrapper, pushSpy } = await toStep2('/login?redirect=%2Fqueue')
      api.post.mockResolvedValueOnce({ data: { access_token: 'tok', username: 'admin' } })
      await wrapper.find('[data-test="mfa-code"]').setValue('123456')
      await wrapper.find('form').trigger('submit.prevent')
      await flushAll()
      expect(api.post).toHaveBeenLastCalledWith('/auth/login/mfa', { mfa_token: 't'.repeat(43), code: '123456' })
      expect(pushSpy).toHaveBeenCalledWith('/queue')
    })

    it('backup code toggle', async () => {
      const { wrapper } = await toStep2()
      const input = () => wrapper.find('[data-test="mfa-code"]')
      expect(input().attributes('inputmode')).toBe('numeric')
      await wrapper.find('[data-test="mfa-toggle-backup"]').trigger('click')
      expect(input().attributes('inputmode')).toBe('text')
      expect(input().attributes('autocomplete')).not.toBe('one-time-code')
      await wrapper.find('[data-test="mfa-toggle-backup"]').trigger('click')
      expect(input().attributes('inputmode')).toBe('numeric')
    })

    it('shows the server detail on a wrong code and stays on step 2', async () => {
      const { wrapper, pushSpy } = await toStep2()
      api.post.mockRejectedValueOnce({ response: { status: 401, data: { detail: 'Code invalide', code: 'mfa_code_invalid' } } })
      await wrapper.find('[data-test="mfa-code"]').setValue('000000')
      await wrapper.find('form').trigger('submit.prevent')
      await flushAll()
      expect(wrapper.text()).toContain('Code invalide')
      expect(wrapper.find('[data-test="mfa-code"]').exists()).toBe(true)
      expect(pushSpy).not.toHaveBeenCalled()
    })

    it('expired challenge returns to step 1', async () => {
      const { wrapper } = await toStep2()
      api.post.mockRejectedValueOnce({ response: { status: 401, data: { detail: 'Session de connexion expirée — reconnectez-vous', code: 'mfa_challenge_invalid' } } })
      await wrapper.find('[data-test="mfa-code"]').setValue('000000')
      await wrapper.find('form').trigger('submit.prevent')
      await flushAll()
      expect(wrapper.find('[data-test="mfa-code"]').exists()).toBe(false)
      expect(wrapper.find('input[type="password"]').exists()).toBe(true)
      expect(wrapper.text()).toContain('Session de connexion expirée')
    })

    it('back button', async () => {
      const { wrapper } = await toStep2()
      await wrapper.find('[data-test="mfa-back"]').trigger('click')
      await flushAll()
      expect(wrapper.find('[data-test="mfa-code"]').exists()).toBe(false)
      expect(wrapper.find('input[type="password"]').exists()).toBe(true)
    })

    it('shows a generic fallback when the error carries no detail', async () => {
      const { wrapper } = await toStep2()
      api.post.mockRejectedValueOnce(new Error('network'))
      await wrapper.find('[data-test="mfa-code"]').setValue('000000')
      await wrapper.find('form').trigger('submit.prevent')
      await flushAll()
      expect(wrapper.find('p.text-red-400').exists()).toBe(true)
    })
  })
})
