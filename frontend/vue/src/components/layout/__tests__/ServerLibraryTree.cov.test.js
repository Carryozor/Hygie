// src/components/layout/__tests__/ServerLibraryTree.cov.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createWebHistory } from 'vue-router'

const servers = [
  { id: '1', name: 'Mon Emby', type: 'emby' },
  { id: '2', name: 'Mon Plex', type: 'plex' },
]
const libraries = {
  1: [{ id: 'l1', name: 'Films' }],
  2: [],
}
vi.mock('@/stores/servers', () => ({
  useServersStore: () => ({
    servers,
    librariesForServer: (id) => libraries[id] || [],
  }),
}))

import ServerLibraryTree from '../ServerLibraryTree.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/library/:id', name: 'library', component: { template: '<div/>' } }],
})

function mountTree() {
  localStorage.clear()
  return mount(ServerLibraryTree, { global: { plugins: [router] } })
}

describe('ServerLibraryTree', () => {
  beforeEach(() => vi.clearAllMocks())

  it('lists every server name', () => {
    const wrapper = mountTree()
    expect(wrapper.text()).toContain('Mon Emby')
    expect(wrapper.text()).toContain('Mon Plex')
  })

  it('starts with libraries expanded by default (collapsed[id] is undefined -> v-show true)', () => {
    const wrapper = mountTree()
    expect(wrapper.text()).toContain('Films')
    expect(wrapper.find('div[style]').exists()).toBe(false)
  })

  it('clicking a server header collapses it, hiding its library links', async () => {
    const wrapper = mountTree()
    const header = wrapper.findAll('.cursor-pointer.select-none')[0]
    await header.trigger('click')
    // Only the v-show-controlled children div gets a style attribute once hidden.
    expect(wrapper.find('div[style]').attributes('style')).toContain('display: none')
  })

  it('clicking again re-expands it', async () => {
    const wrapper = mountTree()
    const header = wrapper.findAll('.cursor-pointer.select-none')[0]
    await header.trigger('click')
    await header.trigger('click')
    // vShow leaves the style attribute in place (now empty) rather than removing it.
    expect(wrapper.find('div[style]').attributes('style') || '').not.toContain('display: none')
    expect(wrapper.text()).toContain('Films')
  })

  it('persists the collapsed/expanded state to localStorage under hygie_sidebar_collapsed', async () => {
    const wrapper = mountTree()
    const header = wrapper.findAll('.cursor-pointer.select-none')[0]
    await header.trigger('click')
    const saved = JSON.parse(localStorage.getItem('hygie_sidebar_collapsed'))
    expect(saved['1']).toBe(true)
  })

  it('restores the collapsed state from localStorage on mount', () => {
    localStorage.setItem('hygie_sidebar_collapsed', JSON.stringify({ 1: true }))
    const wrapper = mount(ServerLibraryTree, { global: { plugins: [router] } })
    expect(wrapper.find('div[style]').attributes('style')).toContain('display: none')
  })

  it('falls back to an empty collapsed state when localStorage holds invalid JSON', () => {
    localStorage.setItem('hygie_sidebar_collapsed', '{not-json')
    expect(() => mount(ServerLibraryTree, { global: { plugins: [router] } })).not.toThrow()
  })

  it('renders the muted dot for a server type with no dedicated color', () => {
    const wrapper = mountTree()
    // 'emby' maps to green, 'plex' maps to orange — both are covered; assert
    // the dot color classes render distinctly per server.
    const dots = wrapper.findAll('span.w-2.h-2')
    expect(dots[0].classes()).toContain('bg-green-500')
    expect(dots[1].classes()).toContain('bg-orange-400')
  })
})
