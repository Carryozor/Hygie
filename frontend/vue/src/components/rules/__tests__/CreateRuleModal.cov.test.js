// src/components/rules/__tests__/CreateRuleModal.cov.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { i18n } from '@/i18n'

vi.mock('@/api/client', () => ({ default: { get: vi.fn().mockResolvedValue({ data: [] }) } }))
vi.mock('@/stores/servers', () => ({
  useServersStore: () => ({ servers: [], libraries: [], fetch: vi.fn().mockResolvedValue(undefined), librariesForServer: () => [] }),
}))

import CreateRuleModal from '../CreateRuleModal.vue'

function mountModal(props) {
  return mount(CreateRuleModal, {
    props: { open: true, ...props },
    global: { plugins: [i18n], stubs: { teleport: true } },
  })
}

describe('CreateRuleModal', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders nothing when open is false', () => {
    const wrapper = mountModal({ open: false })
    expect(wrapper.find('.fixed').exists()).toBe(false)
  })

  it('shows the rule-type selector for a new rule (no editRule)', () => {
    const wrapper = mountModal()
    expect(wrapper.text()).toContain(i18n.global.t('rules.type'))
  })

  it('save button is disabled before a rule type is chosen', () => {
    const wrapper = mountModal()
    const saveBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('common.save'))
    expect(saveBtn.attributes('disabled')).toBeDefined()
  })

  it('emits close when the close (X) button is clicked', async () => {
    const wrapper = mountModal()
    await wrapper.find('.fa-xmark').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('emits close when the cancel button in the footer is clicked', async () => {
    const wrapper = mountModal()
    const cancelBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('common.cancel'))
    await cancelBtn.trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('emits close when clicking the backdrop', async () => {
    const wrapper = mountModal()
    await wrapper.find('.fixed').trigger('mousedown')
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('expert rule: save stays disabled until a name is present (default condition group already satisfies hasGroups)', async () => {
    const wrapper = mountModal({ editType: 'expert' })
    await flushPromises()
    const saveBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('common.save'))
    // A default condition group with days_not_watched exists, but name is empty
    expect(saveBtn.attributes('disabled')).toBeDefined()
  })

  it('expert rule: save becomes enabled once a name is provided (default condition group already satisfies hasGroups)', async () => {
    const wrapper = mountModal({ editType: 'expert' })
    await flushPromises()
    await wrapper.find('input[type=text]').setValue('Ma règle experte')
    await flushPromises()
    const saveBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('common.save'))
    expect(saveBtn.attributes('disabled')).toBeUndefined()
  })

  it('simple rule: save requires a seerr_user_id (stays disabled with none selected)', async () => {
    const wrapper = mountModal({ editType: 'simple' })
    await flushPromises()
    const saveBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('common.save'))
    expect(saveBtn.attributes('disabled')).toBeDefined()
  })

  it('clicking save emits "saved" with {type, data, done} and sets the saving spinner until done() is called', async () => {
    const wrapper = mountModal({ editType: 'expert' })
    await flushPromises()
    await wrapper.find('input[type=text]').setValue('Ma règle')
    await flushPromises()
    const saveBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('common.save'))
    await saveBtn.trigger('click')
    const payload = wrapper.emitted('saved')[0][0]
    expect(payload.type).toBe('expert')
    expect(payload.data.name).toBe('Ma règle')
    expect(typeof payload.done).toBe('function')
    // saving flag now true -> save button shows spinner icon
    expect(wrapper.find('.fa-spinner').exists()).toBe(true)
    payload.done()
    await flushPromises()
    expect(wrapper.find('.fa-spinner').exists()).toBe(false)
  })

  it('double-clicking save while already saving does not emit a second "saved" event (guards against duplicate submits)', async () => {
    const wrapper = mountModal({ editType: 'expert' })
    await flushPromises()
    await wrapper.find('input[type=text]').setValue('Ma règle')
    await flushPromises()
    const saveBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('common.save'))
    await saveBtn.trigger('click')
    await saveBtn.trigger('click')
    expect(wrapper.emitted('saved')).toHaveLength(1)
  })

  it('shows the edit title and pre-fills formData when editRule is provided', () => {
    const wrapper = mountModal({ editRule: { name: 'Existing rule', condition_groups: [] }, editType: 'expert' })
    expect(wrapper.text()).toContain(i18n.global.t('rules.edit'))
    expect(wrapper.text()).not.toContain(i18n.global.t('rules.type')) // type selector hidden when editing
  })

  it('resets formData when the modal is closed and reopened for a new rule (stale name from a previous open cannot leak into the next save)', async () => {
    const wrapper = mountModal({ editType: 'expert' })
    await flushPromises()
    await wrapper.find('input[type=text]').setValue('Ancienne règle')
    await flushPromises()
    let saveBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('common.save'))
    expect(saveBtn.attributes('disabled')).toBeUndefined() // name present -> enabled

    await wrapper.setProps({ open: false })
    await wrapper.setProps({ open: true })
    await flushPromises()

    saveBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('common.save'))
    expect(saveBtn.attributes('disabled')).toBeDefined() // formData reset -> name empty again
  })
})
