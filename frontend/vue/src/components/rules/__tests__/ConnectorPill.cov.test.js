// src/components/rules/__tests__/ConnectorPill.cov.test.js
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ConnectorPill from '../ConnectorPill.vue'

describe('ConnectorPill', () => {
  it('displays the current operator text', () => {
    const wrapper = mount(ConnectorPill, { props: { modelValue: 'AND' } })
    expect(wrapper.find('button').text()).toBe('AND')
  })

  it('applies blue styling for AND and orange styling for OR', () => {
    const and = mount(ConnectorPill, { props: { modelValue: 'AND' } })
    expect(and.find('button').classes()).toContain('bg-blue-500/20')
    const or = mount(ConnectorPill, { props: { modelValue: 'OR' } })
    expect(or.find('button').classes()).toContain('bg-orange-500/20')
  })

  it('emits "OR" when clicked while AND', async () => {
    const wrapper = mount(ConnectorPill, { props: { modelValue: 'AND' } })
    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toEqual([['OR']])
  })

  it('emits "AND" when clicked while OR', async () => {
    const wrapper = mount(ConnectorPill, { props: { modelValue: 'OR' } })
    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toEqual([['AND']])
  })

  it('defaults to "AND" when modelValue is not provided', () => {
    const wrapper = mount(ConnectorPill)
    expect(wrapper.find('button').text()).toBe('AND')
  })
})
