// src/components/ui/__tests__/ConfirmModal.cov.test.js
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { i18n } from '@/i18n'
import ConfirmModal from '../ConfirmModal.vue'

function mountModal(props) {
  return mount(ConfirmModal, {
    props: { show: true, ...props },
    // Teleport renders into document.body by default in jsdom; stub it so the
    // dialog stays in the wrapper's own tree and wrapper.find()/trigger() work.
    global: { plugins: [i18n], stubs: { teleport: true } },
  })
}

describe('ConfirmModal', () => {
  it('renders nothing (no dialog content) when show is false', () => {
    const wrapper = mountModal({ show: false })
    expect(wrapper.find('.fixed').exists()).toBe(false)
  })

  it('renders the dialog when show is true', () => {
    const wrapper = mountModal()
    expect(wrapper.find('.fixed').exists()).toBe(true)
  })

  it('shows the provided message text', () => {
    const wrapper = mountModal({ message: 'Supprimer ce média ?' })
    expect(wrapper.text()).toContain('Supprimer ce média ?')
  })

  it('emits confirm when the confirm button is clicked', async () => {
    const wrapper = mountModal({ confirmLabel: 'Supprimer' })
    const buttons = wrapper.findAll('button')
    const confirmBtn = buttons.find(b => b.text() === 'Supprimer')
    await confirmBtn.trigger('click')
    expect(wrapper.emitted('confirm')).toHaveLength(1)
    expect(wrapper.emitted('cancel')).toBeUndefined()
  })

  it('emits cancel when the cancel button is clicked', async () => {
    const wrapper = mountModal({ cancelLabel: 'Annuler' })
    const buttons = wrapper.findAll('button')
    const cancelBtn = buttons.find(b => b.text() === 'Annuler')
    await cancelBtn.trigger('click')
    expect(wrapper.emitted('cancel')).toHaveLength(1)
    expect(wrapper.emitted('confirm')).toBeUndefined()
  })

  it('emits cancel when the backdrop itself is clicked (mousedown.self)', async () => {
    const wrapper = mountModal()
    await wrapper.find('.fixed').trigger('mousedown')
    expect(wrapper.emitted('cancel')).toHaveLength(1)
  })

  it('does not emit cancel when clicking inside the dialog panel (mousedown.self guard)', async () => {
    const wrapper = mountModal({ message: 'Confirmer ?' })
    await wrapper.find('.rounded-2xl').trigger('mousedown')
    expect(wrapper.emitted('cancel')).toBeUndefined()
  })

  it('falls back to i18n default labels when confirmLabel/cancelLabel are not provided', () => {
    const wrapper = mountModal()
    const buttons = wrapper.findAll('button').map(b => b.text())
    expect(buttons).toContain(String(i18n.global.t('common.cancel')))
    expect(buttons).toContain(String(i18n.global.t('common.confirm')))
  })

  it('renders slot content instead of the default message paragraph when a slot is given', () => {
    const wrapper = mount(ConfirmModal, {
      props: { show: true, message: 'ignored' },
      global: { plugins: [i18n], stubs: { teleport: true } },
      slots: { default: '<p class="custom-slot">Custom warning</p>' },
    })
    expect(wrapper.find('.custom-slot').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('ignored')
  })
})
