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
})
