// src/components/rules/__tests__/LogicRecap.cov.test.js
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { i18n } from '@/i18n'

const libraries = [
  { id: '1', name: 'Films', server_id: '10' },
  { id: '2', name: 'Series', server_id: '20' },
]
const servers = [
  { id: '10', enabled: true },
  { id: '20', enabled: false }, // disabled — its libraries must be excluded from the recap
]

vi.mock('@/stores/servers', () => ({
  useServersStore: () => ({ servers, libraries }),
}))

import LogicRecap from '../LogicRecap.vue'

function mountRecap(props) {
  return mount(LogicRecap, { props, global: { plugins: [i18n] } })
}

describe('LogicRecap', () => {
  // NOTE: hasContent = conditionGroups.length > 0 || libraryIds !== undefined.
  // libraryIds' prop default is `null` (not undefined), and Vue substitutes
  // the default whenever a parent passes `undefined`, so `libraryIds !== undefined`
  // is always true through any real prop usage — the "render nothing" branch is
  // unreachable from outside the component. Not tested (dead branch, not a bug
  // to encode as expected behavior); see final report.

  it('shows "all libraries" text when libraryIds is null', () => {
    const wrapper = mountRecap({ conditionGroups: [], libraryIds: null })
    expect(wrapper.text()).toContain(i18n.global.t('rules.recap.allLibraries'))
  })

  it('lists only library names belonging to enabled servers', () => {
    const wrapper = mountRecap({ conditionGroups: [], libraryIds: ['1', '2'] })
    expect(wrapper.text()).toContain('Films')
    expect(wrapper.text()).not.toContain('Series')
  })

  it('shows "none selected" when libraryIds is an empty array', () => {
    const wrapper = mountRecap({ conditionGroups: [], libraryIds: [] })
    expect(wrapper.text()).toContain(i18n.global.t('rules.recap.noneSelected'))
  })

  it('humanizes a single condition as "field op value"', () => {
    const wrapper = mountRecap({
      conditionGroups: [{ operator: 'AND', conditions: [{ field: 'rating', op: 'gt', value: 5 }] }],
      libraryIds: null,
    })
    expect(wrapper.text()).toContain('>')
    expect(wrapper.text()).toContain('5')
  })

  it('renders array values wrapped in curly braces', () => {
    const wrapper = mountRecap({
      conditionGroups: [{ operator: 'AND', conditions: [{ field: 'rating', op: 'in', value: [1, 2] }] }],
      libraryIds: null,
    })
    expect(wrapper.text()).toContain('{1, 2}')
  })

  it('shows the group operator (OR) between two condition groups', () => {
    const wrapper = mountRecap({
      conditionGroups: [
        { operator: 'AND', conditions: [{ field: 'rating', op: 'gt', value: 5 }] },
        { operator: 'AND', conditions: [{ field: 'play_count', op: 'eq', value: 0 }] },
      ],
      operator: 'OR',
      libraryIds: null,
    })
    expect(wrapper.text()).toContain('OR')
  })
})
