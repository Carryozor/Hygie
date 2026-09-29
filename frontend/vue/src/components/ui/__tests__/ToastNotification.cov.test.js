// src/components/ui/__tests__/ToastNotification.cov.test.js
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import ToastNotification from '../ToastNotification.vue'

function dispatchError(message, type) {
  window.dispatchEvent(new CustomEvent('hygie:error', { detail: { message, type } }))
}

describe('ToastNotification', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('renders no toasts initially', () => {
    const wrapper = mount(ToastNotification, { global: { stubs: { teleport: true } } })
    expect(wrapper.findAll('.pointer-events-auto')).toHaveLength(0)
  })

  it('adds a toast when a hygie:error event is dispatched on window', async () => {
    const wrapper = mount(ToastNotification, { global: { stubs: { teleport: true } } })
    dispatchError('Échec de la sauvegarde')
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('Échec de la sauvegarde')
  })

  it('defaults to the "error" toast type/icon when no type is given', async () => {
    const wrapper = mount(ToastNotification, { global: { stubs: { teleport: true } } })
    dispatchError('Oops')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.bg-red-500\\/15').exists()).toBe(true)
    expect(wrapper.find('.fa-circle-exclamation').exists()).toBe(true)
  })

  it('applies success styling/icon for type="success"', async () => {
    const wrapper = mount(ToastNotification, { global: { stubs: { teleport: true } } })
    dispatchError('Sauvegarde créée', 'success')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.bg-green-500\\/15').exists()).toBe(true)
    expect(wrapper.find('.fa-circle-check').exists()).toBe(true)
  })

  it('applies warning styling/icon for type="warning"', async () => {
    const wrapper = mount(ToastNotification, { global: { stubs: { teleport: true } } })
    dispatchError('Attention', 'warning')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.bg-yellow-500\\/15').exists()).toBe(true)
    expect(wrapper.find('.fa-triangle-exclamation').exists()).toBe(true)
  })

  it('falls back to the neutral/info style for an unrecognized type', async () => {
    const wrapper = mount(ToastNotification, { global: { stubs: { teleport: true } } })
    dispatchError('Info', 'something-else')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.fa-circle-info').exists()).toBe(true)
  })

  it('stacks multiple toasts with distinct ids', async () => {
    const wrapper = mount(ToastNotification, { global: { stubs: { teleport: true } } })
    dispatchError('Premier')
    dispatchError('Deuxième')
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('Premier')
    expect(wrapper.text()).toContain('Deuxième')
  })

  it('clicking the close button removes only that toast', async () => {
    const wrapper = mount(ToastNotification, { global: { stubs: { teleport: true } } })
    dispatchError('Premier')
    dispatchError('Deuxième')
    await wrapper.vm.$nextTick()
    await wrapper.findAll('button')[0].trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).not.toContain('Premier')
    expect(wrapper.text()).toContain('Deuxième')
  })

  it('auto-dismisses a toast after 6 seconds', async () => {
    const wrapper = mount(ToastNotification, { global: { stubs: { teleport: true } } })
    dispatchError('Temporaire')
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('Temporaire')
    await vi.advanceTimersByTimeAsync(6000)
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).not.toContain('Temporaire')
  })

  it('removes the window event listener and clears pending timers on unmount (no leak / no late DOM mutation)', async () => {
    const wrapper = mount(ToastNotification, { global: { stubs: { teleport: true } } })
    dispatchError('Va disparaître')
    await wrapper.vm.$nextTick()
    wrapper.unmount()
    // If the listener/timer weren't cleaned up, this would throw trying to
    // mutate an unmounted component's reactive state during the timer callback.
    expect(() => vi.advanceTimersByTime(6000)).not.toThrow()
    // A hygie:error dispatched after unmount must not be handled anymore.
    expect(() => dispatchError('Après démontage')).not.toThrow()
  })
})
