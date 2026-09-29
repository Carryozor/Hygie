// src/components/settings/__tests__/AlertRow.cov.test.js
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AlertRow from '../AlertRow.vue'

function mountRow(props) {
  return mount(AlertRow, { props: { label: 'Suppression', enabled: 'false', mention: '', msg: '', ...props } })
}

describe('AlertRow', () => {
  it('treats enabled="true" (string) as enabled', () => {
    const wrapper = mountRow({ enabled: 'true' })
    expect(wrapper.find('input[type=checkbox]').element.checked).toBe(true)
  })

  it('treats enabled="false" (string) as disabled', () => {
    const wrapper = mountRow({ enabled: 'false' })
    expect(wrapper.find('input[type=checkbox]').element.checked).toBe(false)
  })

  it('treats boolean true as enabled too (defensive prop coercion)', () => {
    const wrapper = mountRow({ enabled: true })
    expect(wrapper.find('input[type=checkbox]').element.checked).toBe(true)
  })

  it('hides the mention/message inputs when disabled', () => {
    const wrapper = mountRow({ enabled: 'false' })
    expect(wrapper.findAll('input[type=text]')).toHaveLength(0)
  })

  it('shows the mention/message inputs when enabled', () => {
    const wrapper = mountRow({ enabled: 'true', mention: '@admin', msg: 'Attention' })
    const inputs = wrapper.findAll('input[type=text]')
    expect(inputs).toHaveLength(2)
    expect(inputs[0].element.value).toBe('@admin')
    expect(inputs[1].element.value).toBe('Attention')
  })

  it('toggling the slider emits update:enabled as the STRING "true", not a boolean', async () => {
    const wrapper = mountRow({ enabled: 'false' })
    await wrapper.find('input[type=checkbox]').setValue(true)
    expect(wrapper.emitted('update:enabled')).toEqual([['true']])
  })

  it('toggling off emits update:enabled as the string "false"', async () => {
    const wrapper = mountRow({ enabled: 'true' })
    await wrapper.find('input[type=checkbox]').setValue(false)
    expect(wrapper.emitted('update:enabled')).toEqual([['false']])
  })

  it('typing in the mention field emits update:mention with the raw input value', async () => {
    const wrapper = mountRow({ enabled: 'true' })
    await wrapper.findAll('input[type=text]')[0].setValue('@moderators')
    expect(wrapper.emitted('update:mention')).toEqual([['@moderators']])
  })

  it('typing in the message field emits update:msg with the raw input value', async () => {
    const wrapper = mountRow({ enabled: 'true' })
    await wrapper.findAll('input[type=text]')[1].setValue('Un message perso')
    expect(wrapper.emitted('update:msg')).toEqual([['Un message perso']])
  })
})
