// src/components/rules/__tests__/LibraryTreePicker.cov.test.js
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const libraries = [
  { id: '1', name: 'Films', server_id: '10' },
  { id: '2', name: 'Series', server_id: '10' },
  { id: '3', name: 'Docs', server_id: '20' }, // belongs to a disabled server
]
const servers = [
  { id: '10', name: 'Emby', type: 'emby', enabled: true },
  { id: '20', name: 'Plex', type: 'plex', enabled: false },
]

vi.mock('@/stores/servers', () => ({
  useServersStore: () => ({
    servers,
    librariesForServer: (id) => libraries.filter(l => String(l.server_id) === String(id)),
  }),
}))

import LibraryTreePicker from '../LibraryTreePicker.vue'

function mountPicker(modelValue = null) {
  return mount(LibraryTreePicker, { props: { modelValue } })
}

describe('LibraryTreePicker', () => {
  it('excludes servers that are disabled from the tree entirely', () => {
    const wrapper = mountPicker(null)
    expect(wrapper.text()).toContain('Emby')
    expect(wrapper.text()).not.toContain('Plex')
  })

  it('checks the "all libraries" radio when modelValue is null', () => {
    const wrapper = mountPicker(null)
    expect(wrapper.find('input[type=radio]').element.checked).toBe(true)
  })

  it('emits null when the "all libraries" radio is selected', async () => {
    const wrapper = mountPicker(['1'])
    await wrapper.find('input[type=radio]').setValue(true)
    expect(wrapper.emitted('update:modelValue')[0]).toEqual([null])
  })

  it('toggling an individual library checkbox on adds its id (as string) to the selection', async () => {
    const wrapper = mountPicker(null)
    // expand the server node first (children are v-show, hidden but present — but
    // toggleLib is only reachable once expanded in the real UI; clicking the checkbox
    // works regardless of visibility since v-show only sets display:none)
    const checkbox = wrapper.find('input[type=checkbox]')
    await checkbox.setValue(true)
    expect(wrapper.emitted('update:modelValue')[0]).toEqual([['1']])
  })

  it('toggling an already-selected library checkbox off removes it, falling back to null when empty', async () => {
    const wrapper = mountPicker(['1'])
    const checkbox = wrapper.find('input[type=checkbox]')
    await checkbox.setValue(false)
    expect(wrapper.emitted('update:modelValue')[0]).toEqual([null])
  })

  it('clicking the server-level checkbox selects all of its libraries at once', async () => {
    const wrapper = mountPicker(null)
    const serverCheckbox = wrapper.find('.w-4.h-4')
    await serverCheckbox.trigger('click')
    const emitted = wrapper.emitted('update:modelValue')[0][0]
    expect(emitted.sort()).toEqual(['1', '2'])
  })

  it('clicking the server-level checkbox again (fully selected) deselects all its libraries', async () => {
    const wrapper = mountPicker(['1', '2'])
    const serverCheckbox = wrapper.find('.w-4.h-4')
    await serverCheckbox.trigger('click')
    expect(wrapper.emitted('update:modelValue')[0]).toEqual([null])
  })

  it('expanding a server node toggles v-show display on its library children', async () => {
    // jsdom has no layout engine, so VTU's isVisible()/offsetParent heuristics are
    // unreliable here — assert directly on the v-show-controlled inline style instead.
    const wrapper = mountPicker(null)
    expect(wrapper.find('.ml-6').attributes('style')).toContain('display: none')
    await wrapper.find('.fa-chevron-right').trigger('click')
    expect(wrapper.find('.ml-6').attributes('style') || '').not.toContain('display: none')
  })
})
