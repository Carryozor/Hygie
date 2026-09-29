// src/components/ui/__tests__/StatCard.cov.test.js
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import StatCard from '../StatCard.vue'

describe('StatCard', () => {
  it('renders the label and icon', () => {
    const wrapper = mount(StatCard, { props: { label: 'File', value: 5, icon: 'fa-clock' } })
    expect(wrapper.text()).toContain('File')
    expect(wrapper.find('i').classes()).toContain('fa-clock')
  })

  it('formats a plain number with French locale grouping by default', () => {
    const wrapper = mount(StatCard, { props: { label: 'Total', value: 12345 } })
    // toLocaleString('fr-FR') groups with a narrow no-break space (U+202F) in Node's ICU data
    expect((12345).toLocaleString('fr-FR')).toMatch(/^12.345$/)
    expect(wrapper.find('.text-3xl').text()).toBe((12345).toLocaleString('fr-FR'))
  })

  it('formats bytes below 1KB as "B"', () => {
    const wrapper = mount(StatCard, { props: { label: 'Taille', value: 512, format: 'bytes' } })
    expect(wrapper.text()).toContain('512 B')
  })

  it('formats bytes in the KB range', () => {
    const wrapper = mount(StatCard, { props: { label: 'Taille', value: 2048, format: 'bytes' } })
    expect(wrapper.text()).toContain('2.0 KB')
  })

  it('formats bytes in the MB range', () => {
    const wrapper = mount(StatCard, { props: { label: 'Taille', value: 5 * 1024 ** 2, format: 'bytes' } })
    expect(wrapper.text()).toContain('5.0 MB')
  })

  it('formats bytes in the GB range', () => {
    const wrapper = mount(StatCard, { props: { label: 'Taille', value: 3 * 1024 ** 3, format: 'bytes' } })
    expect(wrapper.text()).toContain('3.0 GB')
  })

  it('renders a raw string value unmodified when format is "string"', () => {
    const wrapper = mount(StatCard, { props: { label: 'Statut', value: 'En cours', format: 'string' } })
    expect(wrapper.text()).toContain('En cours')
  })

  it('shows the sub text when provided', () => {
    const wrapper = mount(StatCard, { props: { label: 'File', value: 1, sub: 'depuis hier' } })
    expect(wrapper.text()).toContain('depuis hier')
  })

  it('omits the sub line entirely when sub is not provided', () => {
    const wrapper = mount(StatCard, { props: { label: 'File', value: 1 } })
    // The sub <div> only renders with v-if="sub" — assert it isn't in the DOM
    const divs = wrapper.findAll('div.text-xs')
    expect(divs.length).toBe(0)
  })

  it('applies the red color theme classes when color="red"', () => {
    const wrapper = mount(StatCard, { props: { label: 'Erreurs', value: 2, color: 'red' } })
    expect(wrapper.find('.text-3xl').classes()).toContain('text-red-300')
  })

  it('falls back to the accent theme for an unknown color value', () => {
    const wrapper = mount(StatCard, { props: { label: 'X', value: 1, color: 'not-a-real-color' } })
    expect(wrapper.find('.text-3xl').classes()).toContain('text-white')
  })
})
