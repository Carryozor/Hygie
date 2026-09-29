// src/components/queue/__tests__/QueueGridCard.cov.test.js
//
// Like QueueListRow, the title link goes through safeUrl() — same
// javascript: URL risk (server-controlled seerr_request_url), same guard.
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { i18n } from '@/i18n'
import QueueGridCard from '../QueueGridCard.vue'

function baseItem(overrides = {}) {
  return { title: 'Dune', media_type: 'movie', status: 'pending', delete_at: '2026-10-01T00:00:00Z', ...overrides }
}

const helperProps = {
  daysLabel: () => '3j', gridBannerClass: () => 'bg-red-500', isSeries: (t) => t === 'show',
}

function mountCard(props) {
  return mount(QueueGridCard, { props: { item: baseItem(), ...helperProps, ...props }, global: { plugins: [i18n] } })
}

describe('QueueGridCard', () => {
  it('renders the title as plain text when there is no seerr_request_url', () => {
    const wrapper = mountCard()
    expect(wrapper.find('a').exists()).toBe(false)
    expect(wrapper.text()).toContain('Dune')
  })

  it('renders the title as a safe link when seerr_request_url is a normal URL', () => {
    const wrapper = mountCard({ item: baseItem({ seerr_request_url: 'https://seerr.example.com/movie/1' }) })
    expect(wrapper.find('a').attributes('href')).toBe('https://seerr.example.com/movie/1')
  })

  it('neutralizes a javascript: seerr_request_url to "#"', () => {
    const wrapper = mountCard({ item: baseItem({ seerr_request_url: 'javascript:alert(1)' }) })
    expect(wrapper.find('a').attributes('href')).toBe('#')
  })

  it('emits delete with the item when the delete overlay button is clicked (pending only)', async () => {
    const wrapper = mountCard({ item: baseItem({ status: 'pending' }) })
    const buttons = wrapper.findAll('button')
    await buttons[0].trigger('click')
    expect(wrapper.emitted('delete')[0][0].title).toBe('Dune')
  })

  it('emits ignore with the item when the ignore overlay button is clicked', async () => {
    const wrapper = mountCard({ item: baseItem({ status: 'pending' }) })
    const buttons = wrapper.findAll('button')
    await buttons[1].trigger('click')
    expect(wrapper.emitted('ignore')[0][0].title).toBe('Dune')
  })

  it('hides the action overlay entirely when status is not pending', () => {
    const wrapper = mountCard({ item: baseItem({ status: 'deleted' }) })
    expect(wrapper.findAll('button')).toHaveLength(0)
  })

  it('dims the card and shows "serveur off" when serverDisabled is true', () => {
    const wrapper = mountCard({ serverDisabled: true })
    expect(wrapper.find('.opacity-40').exists()).toBe(true)
    expect(wrapper.text()).toContain('serveur off')
  })

  it('does not show the serveur-off hint when serverDisabled is false', () => {
    const wrapper = mountCard({ serverDisabled: false })
    expect(wrapper.text()).not.toContain('serveur off')
  })
})
