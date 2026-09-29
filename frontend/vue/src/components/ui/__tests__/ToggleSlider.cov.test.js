// src/components/ui/__tests__/ToggleSlider.cov.test.js
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ToggleSlider from '../ToggleSlider.vue'

describe('ToggleSlider', () => {
  it('renders unchecked when modelValue is false', () => {
    const wrapper = mount(ToggleSlider, { props: { modelValue: false } })
    expect(wrapper.find('input[type=checkbox]').element.checked).toBe(false)
  })

  it('renders checked when modelValue is true', () => {
    const wrapper = mount(ToggleSlider, { props: { modelValue: true } })
    expect(wrapper.find('input[type=checkbox]').element.checked).toBe(true)
  })

  it('emits update:modelValue with the inverse of current checked state on click', async () => {
    const wrapper = mount(ToggleSlider, { props: { modelValue: false } })
    await wrapper.find('input[type=checkbox]').setValue(true)
    expect(wrapper.emitted('update:modelValue')).toEqual([[true]])
  })

  it('emits false when toggling an initially-true slider off', async () => {
    const wrapper = mount(ToggleSlider, { props: { modelValue: true } })
    await wrapper.find('input[type=checkbox]').setValue(false)
    expect(wrapper.emitted('update:modelValue')).toEqual([[false]])
  })

  it('reflects an external modelValue change made after mount (parent controls state)', async () => {
    const wrapper = mount(ToggleSlider, { props: { modelValue: false } })
    await wrapper.setProps({ modelValue: true })
    expect(wrapper.find('input[type=checkbox]').element.checked).toBe(true)
  })

  it('disables the input and blocks interaction when disabled prop is true', () => {
    const wrapper = mount(ToggleSlider, { props: { modelValue: false, disabled: true } })
    expect(wrapper.find('input[type=checkbox]').element.disabled).toBe(true)
  })
})
