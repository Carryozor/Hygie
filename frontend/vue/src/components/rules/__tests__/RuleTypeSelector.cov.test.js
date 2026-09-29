// src/components/rules/__tests__/RuleTypeSelector.cov.test.js
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { i18n } from '@/i18n'
import RuleTypeSelector from '../RuleTypeSelector.vue'

function mountSelector(modelValue = '') {
  return mount(RuleTypeSelector, { props: { modelValue }, global: { plugins: [i18n] } })
}

describe('RuleTypeSelector', () => {
  it('renders exactly the "simple" and "expert" options', () => {
    const wrapper = mountSelector()
    const buttons = wrapper.findAll('button')
    expect(buttons).toHaveLength(2)
    expect(buttons[0].text()).toContain(i18n.global.t('rules.simpleSection.title'))
    expect(buttons[1].text()).toContain(i18n.global.t('rules.expertSection.title'))
  })

  it('emits update:modelValue with "simple" when the first card is clicked', async () => {
    const wrapper = mountSelector()
    await wrapper.findAll('button')[0].trigger('click')
    expect(wrapper.emitted('update:modelValue')).toEqual([['simple']])
  })

  it('emits update:modelValue with "expert" when the second card is clicked', async () => {
    const wrapper = mountSelector()
    await wrapper.findAll('button')[1].trigger('click')
    expect(wrapper.emitted('update:modelValue')).toEqual([['expert']])
  })

  it('highlights the currently-selected type with the accent border class', () => {
    const wrapper = mountSelector('expert')
    const buttons = wrapper.findAll('button')
    expect(buttons[1].classes()).toContain('border-[var(--accent)]')
    expect(buttons[0].classes()).not.toContain('border-[var(--accent)]')
  })

  it('highlights neither card when modelValue is empty', () => {
    const wrapper = mountSelector('')
    for (const btn of wrapper.findAll('button')) {
      expect(btn.classes()).not.toContain('border-[var(--accent)]')
    }
  })
})
