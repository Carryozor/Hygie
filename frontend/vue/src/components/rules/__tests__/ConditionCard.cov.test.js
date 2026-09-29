// src/components/rules/__tests__/ConditionCard.cov.test.js
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { i18n } from '@/i18n'
import ConditionCard from '../ConditionCard.vue'

function mountCard(props) {
  return mount(ConditionCard, {
    props: { condition: { field: 'days_not_watched', op: 'gt', value: 30 }, ...props },
    global: { plugins: [i18n] },
  })
}

describe('ConditionCard', () => {
  it('emits an updated condition with the new field, preserving op and value', async () => {
    const wrapper = mountCard()
    await wrapper.find('select').setValue('rating')
    const emitted = wrapper.emitted('update')[0][0]
    expect(emitted.field).toBe('rating')
    expect(emitted.op).toBe('gt')
    expect(emitted.value).toBe(30)
  })

  it('emits remove when the remove (xmark) button is clicked', async () => {
    const wrapper = mountCard()
    // Main row = [drag handle, remove] when no seerr picker is shown; remove is last.
    const buttons = wrapper.findAll('button')
    await buttons[buttons.length - 1].trigger('click')
    expect(wrapper.emitted('remove')).toHaveLength(1)
  })

  it('converts a numeric text input to a Number for a scalar field', async () => {
    const wrapper = mountCard({ condition: { field: 'days_not_watched', op: 'gt', value: 30 } })
    const input = wrapper.find('input[type=text]')
    await input.setValue('45')
    const emitted = wrapper.emitted('update').at(-1)[0]
    expect(emitted.value).toBe(45)
    expect(typeof emitted.value).toBe('number')
  })

  it('keeps a non-numeric scalar value as a raw string', async () => {
    const wrapper = mountCard({ condition: { field: 'media_type', op: 'eq', value: '' } })
    const input = wrapper.find('input[type=text]')
    await input.setValue('movie')
    const emitted = wrapper.emitted('update').at(-1)[0]
    expect(emitted.value).toBe('movie')
  })

  it('parses a comma-separated numeric list into an array of numbers for an "in" op', async () => {
    const wrapper = mountCard({ condition: { field: 'rating', op: 'in', value: [] } })
    const input = wrapper.find('input[type=text]')
    await input.setValue('1, 2, 3')
    const emitted = wrapper.emitted('update').at(-1)[0]
    expect(emitted.value).toEqual([1, 2, 3])
  })

  it('parses a comma-separated non-numeric list into an array of strings for an "in" op', async () => {
    const wrapper = mountCard({ condition: { field: 'media_type', op: 'in', value: [] } })
    const input = wrapper.find('input[type=text]')
    await input.setValue('movie, series')
    const emitted = wrapper.emitted('update').at(-1)[0]
    expect(emitted.value).toEqual(['movie', 'series'])
  })

  it('resets value to an empty array when switching op to "in" from a scalar value', async () => {
    const wrapper = mountCard({ condition: { field: 'rating', op: 'gt', value: 5 } })
    const opSelect = wrapper.findAll('select')[1]
    await opSelect.setValue('in')
    const emitted = wrapper.emitted('update').at(-1)[0]
    expect(emitted.value).toEqual([])
  })

  it('resets value to an empty string when switching op away from "in" from an array value', async () => {
    const wrapper = mountCard({ condition: { field: 'rating', op: 'in', value: [1, 2] } })
    const opSelect = wrapper.findAll('select')[1]
    await opSelect.setValue('gt')
    const emitted = wrapper.emitted('update').at(-1)[0]
    expect(emitted.value).toBe('')
  })

  it('restricts operators to "eq" only for the never_watched boolean field', () => {
    const wrapper = mountCard({ condition: { field: 'never_watched', op: 'eq', value: true } })
    const opSelect = wrapper.findAll('select')[1]
    const options = opSelect.findAll('option').map(o => o.element.value)
    expect(options).toEqual(['eq'])
  })

  it('restricts operators to eq/in/not_in for a text field like media_type', () => {
    const wrapper = mountCard({ condition: { field: 'media_type', op: 'eq', value: '' } })
    const opSelect = wrapper.findAll('select')[1]
    const options = opSelect.findAll('option').map(o => o.element.value)
    expect(options.sort()).toEqual(['eq', 'in', 'not_in'])
  })

  it('offers all 7 operators for a numeric field like rating', () => {
    const wrapper = mountCard({ condition: { field: 'rating', op: 'gt', value: 5 } })
    const opSelect = wrapper.findAll('select')[1]
    expect(opSelect.findAll('option')).toHaveLength(7)
  })

  it('shows the seerr user checkbox picker instead of a plain input when field=seerr_user_id, op is a list op, and users exist', () => {
    const wrapper = mountCard({
      condition: { field: 'seerr_user_id', op: 'in', value: [] },
      seerrUsers: [{ id: 1, username: 'alice' }, { id: 2, username: 'bob' }],
    })
    expect(wrapper.find('input[type=text]').exists()).toBe(false)
    expect(wrapper.findAll('input[type=checkbox]')).toHaveLength(2)
  })

  it('falls back to the plain text input for seerr_user_id when no seerr users are loaded', () => {
    const wrapper = mountCard({ condition: { field: 'seerr_user_id', op: 'in', value: [] }, seerrUsers: [] })
    expect(wrapper.find('input[type=text]').exists()).toBe(true)
  })

  it('toggling a seerr user checkbox adds their id to the selection', async () => {
    const wrapper = mountCard({
      condition: { field: 'seerr_user_id', op: 'in', value: [1] },
      seerrUsers: [{ id: 1, username: 'alice' }, { id: 2, username: 'bob' }],
    })
    const checkboxes = wrapper.findAll('input[type=checkbox]')
    await checkboxes[1].setValue(true) // bob, id=2
    const emitted = wrapper.emitted('update').at(-1)[0]
    expect(emitted.value.sort()).toEqual([1, 2])
  })

  it('toggling an already-selected seerr user checkbox removes their id', async () => {
    const wrapper = mountCard({
      condition: { field: 'seerr_user_id', op: 'in', value: [1, 2] },
      seerrUsers: [{ id: 1, username: 'alice' }, { id: 2, username: 'bob' }],
    })
    const checkboxes = wrapper.findAll('input[type=checkbox]')
    await checkboxes[0].setValue(false) // alice, id=1
    const emitted = wrapper.emitted('update').at(-1)[0]
    expect(emitted.value).toEqual([2])
  })

  it('joins an array value with ", " for display in the plain text input', () => {
    const wrapper = mountCard({ condition: { field: 'rating', op: 'in', value: [1, 2, 3] } })
    expect(wrapper.find('input[type=text]').element.value).toBe('1, 2, 3')
  })
})
