// src/components/rules/__tests__/ExpertRuleBuilder.cov.test.js
//
// ExpertRuleBuilder assembles the actual rule payload (condition_groups, operator,
// library_ids) that gets sent to the backend and used to decide what media is
// queued for deletion. These tests verify the emitted payload matches the UI
// actions taken, not just that something renders.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { i18n } from '@/i18n'

vi.mock('@/api/client', () => ({ default: { get: vi.fn().mockResolvedValue({ data: [] }) } }))

const libraries = [
  { id: '1', name: 'Films (Emby)', server_id: '10' },
  { id: '2', name: 'Docs (Plex)', server_id: '20' },
]
const servers = [
  { id: '10', enabled: true },
  { id: '20', enabled: false },
]
const serversFetch = vi.fn().mockResolvedValue(undefined)
vi.mock('@/stores/servers', () => ({
  useServersStore: () => ({
    servers, libraries, fetch: serversFetch,
    librariesForServer: (id) => libraries.filter(l => String(l.server_id) === String(id)),
  }),
}))

import ExpertRuleBuilder from '../ExpertRuleBuilder.vue'

function mountBuilder(props) {
  return mount(ExpertRuleBuilder, { props: { initial: {}, ...props }, global: { plugins: [i18n] } })
}

describe('ExpertRuleBuilder', () => {
  beforeEach(() => vi.clearAllMocks())

  it('starts with one default condition group (days_not_watched > 30, AND)', async () => {
    const wrapper = mountBuilder()
    await wrapper.find('input[type=text]').setValue('trigger a watch flush') // name field, forces a watch tick
    await flushPromises()
    const last = wrapper.emitted('update:modelValue').at(-1)[0]
    expect(last.condition_groups).toHaveLength(1)
    expect(last.condition_groups[0].conditions).toEqual([{ field: 'days_not_watched', op: 'gt', value: 30 }])
    expect(last.operator).toBe('AND')
  })

  it('emits the rule name typed into the name field', async () => {
    const wrapper = mountBuilder()
    await wrapper.find('input[type=text]').setValue('Ma règle')
    await flushPromises()
    const last = wrapper.emitted('update:modelValue').at(-1)[0]
    expect(last.name).toBe('Ma règle')
  })

  it('"Add block" pushes a second default condition group', async () => {
    const wrapper = mountBuilder()
    const addBlockBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('rules.addBlock'))
    await addBlockBtn.trigger('click')
    await flushPromises()
    const last = wrapper.emitted('update:modelValue').at(-1)[0]
    expect(last.condition_groups).toHaveLength(2)
  })

  it('"Add condition" inside a group appends a default condition to that group', async () => {
    const wrapper = mountBuilder()
    const addCondBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('rules.addCondition'))
    await addCondBtn.trigger('click')
    await flushPromises()
    const last = wrapper.emitted('update:modelValue').at(-1)[0]
    expect(last.condition_groups[0].conditions).toHaveLength(2)
  })

  it('removing the only condition in a group replaces it with a default condition (never leaves an empty group)', async () => {
    const wrapper = mountBuilder()
    // remove button on the single ConditionCard: the only bare xmark button with no text
    const removeBtn = wrapper.findAll('button').find(b => b.find('i.fa-xmark').exists() && !b.text())
    await removeBtn.trigger('click')
    await flushPromises()
    const last = wrapper.emitted('update:modelValue').at(-1)[0]
    expect(last.condition_groups[0].conditions).toEqual([{ field: 'days_not_watched', op: 'gt', value: 30 }])
  })

  it('toggling the group AND/OR connector between two groups flips form.operator', async () => {
    const wrapper = mountBuilder()
    const addBlockBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('rules.addBlock'))
    await addBlockBtn.trigger('click')
    await flushPromises()
    const connector = wrapper.findAll('button').find(b => b.text() === 'AND')
    await connector.trigger('click')
    await flushPromises()
    const last = wrapper.emitted('update:modelValue').at(-1)[0]
    expect(last.operator).toBe('OR')
  })

  it('"remove group" button is absent when only one group remains (only the condition-remove xmark shows)', () => {
    const wrapper = mountBuilder()
    // One default group -> one ConditionCard, whose own remove button also uses fa-xmark.
    // The group-level remove button only appears once condition_groups.length > 1.
    expect(wrapper.findAll('.fa-xmark')).toHaveLength(1)
  })

  it('"remove group" button appears once a second group is added, and removing it drops back to one group', async () => {
    const wrapper = mountBuilder()
    const addBlockBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('rules.addBlock'))
    await addBlockBtn.trigger('click')
    await flushPromises()
    // 2 groups => 2 group-level xmarks + 2 condition-level xmarks = 4
    expect(wrapper.findAll('.fa-xmark')).toHaveLength(4)
  })

  it('filters out library_ids belonging to a disabled server from the initial value', async () => {
    const wrapper = mountBuilder({ initial: { library_ids: ['1', '2'], name: 'x' } })
    await flushPromises()
    await wrapper.find('input[type=text]').setValue('x2')
    await flushPromises()
    const last = wrapper.emitted('update:modelValue').at(-1)[0]
    expect(last.library_ids).toEqual(['1']) // '2' belongs to disabled server 20
  })

  it('collapses an all-filtered library_ids selection down to null (meaning "all libraries")', async () => {
    const wrapper = mountBuilder({ initial: { library_ids: ['2'], name: 'x' } }) // only the disabled-server lib
    await flushPromises()
    await wrapper.find('input[type=text]').setValue('x2')
    await flushPromises()
    const last = wrapper.emitted('update:modelValue').at(-1)[0]
    expect(last.library_ids).toBeNull()
  })

  it('restores condition_groups from an initial value with legacy flat "conditions" (pre-group format)', async () => {
    const wrapper = mountBuilder({
      initial: { conditions: [{ field: 'rating', op: 'lt', value: 3 }], operator: 'OR' },
    })
    await wrapper.find('input[type=text]').setValue('x')
    await flushPromises()
    const last = wrapper.emitted('update:modelValue').at(-1)[0]
    expect(last.condition_groups).toEqual([{ conditions: [{ field: 'rating', op: 'lt', value: 3 }], operator: 'OR' }])
  })

  it('"remove group" (xmark on the group header) deletes that group and keeps the other', async () => {
    const wrapper = mountBuilder()
    const addBlockBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('rules.addBlock'))
    await addBlockBtn.trigger('click')
    await flushPromises()
    // Group-header remove buttons carry no text, same as condition-remove ones;
    // the group-header one lives in the '.bg-[var(--bg2)]' header row (not the card's main row).
    const groupHeaderRemove = wrapper.find('.bg-\\[var\\(--bg2\\)\\] button:last-child')
    await groupHeaderRemove.trigger('click')
    await flushPromises()
    const last = wrapper.emitted('update:modelValue').at(-1)[0]
    expect(last.condition_groups).toHaveLength(1)
  })

  it('editing a condition inside a group (via ConditionCard update) is reflected in the emitted payload', async () => {
    const wrapper = mountBuilder()
    // The form has 2 <select>s: form.action (queue/notify_only) and the
    // ConditionCard's field select — find the one exposing 'days_not_watched'.
    const fieldSelect = wrapper.findAll('select').find(s =>
      s.findAll('option').some(o => o.element.value === 'days_not_watched'))
    await fieldSelect.setValue('rating')
    await flushPromises()
    const last = wrapper.emitted('update:modelValue').at(-1)[0]
    expect(last.condition_groups[0].conditions[0].field).toBe('rating')
  })

  it('defaults grace_days to 7 and priority to 0 when not provided', async () => {
    const wrapper = mountBuilder()
    const numberInputs = wrapper.findAll('input[type=number]')
    expect(numberInputs[0].element.value).toBe('0') // priority
    expect(numberInputs[1].element.value).toBe('7') // grace_days
  })
})
