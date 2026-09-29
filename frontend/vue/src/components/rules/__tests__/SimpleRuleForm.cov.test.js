// src/components/rules/__tests__/SimpleRuleForm.cov.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { i18n } from '@/i18n'

vi.mock('@/api/client', () => ({ default: { get: vi.fn() } }))
const serversFetch = vi.fn().mockResolvedValue(undefined)
vi.mock('@/stores/servers', () => ({
  useServersStore: () => ({ libraries: [], servers: [], fetch: serversFetch }),
}))

import api from '@/api/client'
import SimpleRuleForm from '../SimpleRuleForm.vue'

function mountForm(props) {
  return mount(SimpleRuleForm, { props: { initial: {}, ...props }, global: { plugins: [i18n] } })
}

describe('SimpleRuleForm', () => {
  beforeEach(() => vi.clearAllMocks())

  it('loads seerr users on mount and lists them', async () => {
    api.get.mockResolvedValueOnce({ data: [{ id: 1, username: 'alice', discord_id: '' }] })
    const wrapper = mountForm()
    await flushPromises()
    expect(wrapper.text()).toContain('alice')
  })

  it('shows the "no users" message when the API returns an empty list', async () => {
    api.get.mockResolvedValueOnce({ data: [] })
    const wrapper = mountForm()
    await flushPromises()
    expect(wrapper.text()).toContain(i18n.global.t('rules.simpleSection.noUsers'))
  })

  it('falls back to a single synthetic user entry when the API call fails but initial.seerr_user_id is set', async () => {
    api.get.mockRejectedValueOnce(new Error('network down'))
    const wrapper = mountForm({ initial: { seerr_user_id: 42, seerr_username: 'bob' } })
    await flushPromises()
    expect(wrapper.text()).toContain('bob')
  })

  it('selecting a user sets seerr_user_id/username and emits update:modelValue', async () => {
    api.get.mockResolvedValueOnce({ data: [{ id: 7, username: 'carol', discord_id: '' }] })
    const wrapper = mountForm()
    await flushPromises()
    const userBtn = wrapper.findAll('button').find(b => b.text().includes('carol'))
    await userBtn.trigger('click')
    await flushPromises()
    const last = wrapper.emitted('update:modelValue').at(-1)[0]
    expect(last.seerr_user_id).toBe(7)
    expect(last.seerr_username).toBe('carol')
  })

  it('selecting a user auto-fills discord_id from the user record when the form field is empty', async () => {
    api.get.mockResolvedValueOnce({ data: [{ id: 7, username: 'carol', discord_id: '999888' }] })
    const wrapper = mountForm()
    await flushPromises()
    const userBtn = wrapper.findAll('button').find(b => b.text().includes('carol'))
    await userBtn.trigger('click')
    await flushPromises()
    const last = wrapper.emitted('update:modelValue').at(-1)[0]
    expect(last.discord_id).toBe('999888')
  })

  it('does not overwrite an already-filled discord_id when selecting a different user', async () => {
    api.get.mockResolvedValueOnce({ data: [{ id: 7, username: 'carol', discord_id: '111' }] })
    const wrapper = mountForm({ initial: { discord_id: 'manual-value' } })
    await flushPromises()
    const userBtn = wrapper.findAll('button').find(b => b.text().includes('carol'))
    await userBtn.trigger('click')
    await flushPromises()
    const last = wrapper.emitted('update:modelValue').at(-1)[0]
    expect(last.discord_id).toBe('manual-value')
  })

  it('filters the user list by the search box (case-insensitive substring match)', async () => {
    api.get.mockResolvedValueOnce({
      data: [{ id: 1, username: 'Alice', discord_id: '' }, { id: 2, username: 'Bob', discord_id: '' }],
    })
    const wrapper = mountForm()
    await flushPromises()
    const searchInput = wrapper.find(`input[placeholder="${i18n.global.t('rules.simpleSection.searchUsers')}"]`)
    await searchInput.setValue('ali')
    expect(wrapper.text()).toContain('Alice')
    expect(wrapper.text()).not.toContain('Bob')
  })

  it('defaults grace_days to 30 when not provided in initial', async () => {
    api.get.mockResolvedValueOnce({ data: [] })
    const wrapper = mountForm()
    await flushPromises()
    expect(wrapper.find('input[type=number]').element.value).toBe('30')
  })

  it('defaults enabled to true unless initial.enabled is explicitly false', async () => {
    api.get.mockResolvedValueOnce({ data: [] })
    const wrapper = mountForm({ initial: { enabled: false } })
    await flushPromises()
    expect(wrapper.find('input[type=checkbox]').element.checked).toBe(false)
  })

  it('synthesizes a single user entry when the API succeeds with an empty list but initial.seerr_user_id is set', async () => {
    api.get.mockResolvedValueOnce({ data: [] })
    const wrapper = mountForm({ initial: { seerr_user_id: 9, seerr_username: 'dave' } })
    await flushPromises()
    expect(wrapper.text()).toContain('dave')
  })

  it('typing into the name field updates form.name and is reflected in the emitted payload', async () => {
    api.get.mockResolvedValueOnce({ data: [] })
    const wrapper = mountForm()
    await flushPromises()
    await wrapper.find('input[type=text]').setValue('Ma règle simple')
    const last = wrapper.emitted('update:modelValue').at(-1)[0]
    expect(last.name).toBe('Ma règle simple')
  })

  it('changing grace_days as a number input coerces the value to a Number in the payload', async () => {
    api.get.mockResolvedValueOnce({ data: [] })
    const wrapper = mountForm()
    await flushPromises()
    await wrapper.find('input[type=number]').setValue('45')
    const last = wrapper.emitted('update:modelValue').at(-1)[0]
    expect(last.grace_days).toBe(45)
    expect(typeof last.grace_days).toBe('number')
  })

  it('typing a manual discord_id updates the payload', async () => {
    api.get.mockResolvedValueOnce({ data: [] })
    const wrapper = mountForm()
    await flushPromises()
    const discordInput = wrapper.find(`input[placeholder="${i18n.global.t('rules.discordIdPlaceholder')}"]`)
    await discordInput.setValue('12345')
    const last = wrapper.emitted('update:modelValue').at(-1)[0]
    expect(last.discord_id).toBe('12345')
  })

  it('unchecking the "active" toggle sets enabled to false in the payload', async () => {
    api.get.mockResolvedValueOnce({ data: [] })
    const wrapper = mountForm({ initial: { enabled: true } })
    await flushPromises()
    await wrapper.find('input[type=checkbox]').setValue(false)
    const last = wrapper.emitted('update:modelValue').at(-1)[0]
    expect(last.enabled).toBe(false)
  })
})
