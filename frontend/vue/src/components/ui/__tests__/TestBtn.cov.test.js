// src/components/ui/__tests__/TestBtn.cov.test.js
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('@/api/client', () => ({
  default: { post: vi.fn() },
}))
import api from '@/api/client'
import TestBtn from '../TestBtn.vue'

describe('TestBtn', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('starts idle showing "Tester"', () => {
    const wrapper = mount(TestBtn, { props: { service: 'radarr' } })
    expect(wrapper.find('button').text()).toBe('Tester')
  })

  it('posts to /settings/test/<service> with the service prop when clicked', async () => {
    api.post.mockResolvedValueOnce({ data: { ok: true, message: '' } })
    const wrapper = mount(TestBtn, { props: { service: 'sonarr' } })
    await wrapper.find('button').trigger('click')
    expect(api.post).toHaveBeenCalledWith('/settings/test/sonarr')
  })

  it('shows "✓ OK" and the returned message on success', async () => {
    api.post.mockResolvedValueOnce({ data: { ok: true, message: 'Connexion réussie' } })
    const wrapper = mount(TestBtn, { props: { service: 'radarr' } })
    await wrapper.find('button').trigger('click')
    await vi.waitFor(() => expect(wrapper.find('button').text()).toBe('✓ OK'))
    expect(wrapper.text()).toContain('Connexion réussie')
  })

  it('shows "✗ Erreur" when the API responds ok:false', async () => {
    api.post.mockResolvedValueOnce({ data: { ok: false, message: 'Clé API invalide' } })
    const wrapper = mount(TestBtn, { props: { service: 'radarr' } })
    await wrapper.find('button').trigger('click')
    await vi.waitFor(() => expect(wrapper.find('button').text()).toBe('✗ Erreur'))
    expect(wrapper.text()).toContain('Clé API invalide')
  })

  it('shows "✗ Erreur" with no message when the request rejects (network failure)', async () => {
    api.post.mockRejectedValueOnce(new Error('network down'))
    const wrapper = mount(TestBtn, { props: { service: 'radarr' } })
    await wrapper.find('button').trigger('click')
    await vi.waitFor(() => expect(wrapper.find('button').text()).toBe('✗ Erreur'))
    expect(wrapper.findAll('span.text-xs').length).toBe(0)
  })

  it('resets to idle 6 seconds after a result is shown', async () => {
    api.post.mockResolvedValueOnce({ data: { ok: true, message: 'ok' } })
    const wrapper = mount(TestBtn, { props: { service: 'radarr' } })
    await wrapper.find('button').trigger('click')
    await vi.waitFor(() => expect(wrapper.find('button').text()).toBe('✓ OK'))
    await vi.advanceTimersByTimeAsync(6000)
    expect(wrapper.find('button').text()).toBe('Tester')
  })
})
