// src/components/ui/__tests__/SortHeader.cov.test.js
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SortHeader from '../SortHeader.vue'

function mountHeader(props) {
  return mount(SortHeader, { props: { label: 'Titre', field: 'title', sort: 'title', dir: 'asc', ...props } })
}

describe('SortHeader', () => {
  it('renders the label text', () => {
    const wrapper = mountHeader({ label: 'Ajouté le' })
    expect(wrapper.text()).toContain('Ajouté le')
  })

  it('emits sort with its own field name when clicked', async () => {
    const wrapper = mountHeader({ field: 'added_date' })
    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('sort')).toEqual([['added_date']])
  })

  it('shows the ascending icon when this field is the active sort and dir is asc', () => {
    const wrapper = mountHeader({ field: 'title', sort: 'title', dir: 'asc' })
    expect(wrapper.find('i').classes()).toContain('fa-sort-up')
  })

  it('shows the descending icon when this field is the active sort and dir is desc', () => {
    const wrapper = mountHeader({ field: 'title', sort: 'title', dir: 'desc' })
    expect(wrapper.find('i').classes()).toContain('fa-sort-down')
  })

  it('shows the neutral icon when a different field is the active sort', () => {
    const wrapper = mountHeader({ field: 'title', sort: 'added_date', dir: 'asc' })
    const classes = wrapper.find('i').classes()
    expect(classes).toContain('fa-sort')
    expect(classes).not.toContain('fa-sort-up')
    expect(classes).not.toContain('fa-sort-down')
  })
})
