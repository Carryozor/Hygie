// src/components/queue/__tests__/QueueListRow.cov.test.js
//
// QueueListRow renders two dynamic links (Seerr request URL, requester profile
// URL) built from server-controlled data. Both must go through safeUrl() —
// this is the one thing standing between a stored `javascript:` URL and code
// execution in the authenticated origin (see src/utils/safeUrl.js).
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { i18n } from '@/i18n'
import QueueListRow from '../QueueListRow.vue'

function baseItem(overrides = {}) {
  return {
    title: 'Inception', media_type: 'movie', status: 'pending',
    delete_at: '2026-10-01T00:00:00Z', last_played: null, view_count: 0,
    added_date: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

const helperProps = {
  daysLabel: () => '3j', daysClass: () => 'text-yellow-400', rowUrgencyClass: () => '',
  formatDate: (d) => (d ? `fmt(${d})` : '—'),
  statusLabel: (s) => s, statusClass: () => '', isSeries: (t) => t === 'show',
}

function mountRow(props) {
  return mount(QueueListRow, {
    props: { item: baseItem(), ...helperProps, ...props },
    global: { plugins: [i18n] },
    // QueueListRow renders <tr>/<td> — mount inside a <table> ancestor via a wrapper component
    // is unnecessary for jsdom text/attr assertions, so we mount it directly.
  })
}

describe('QueueListRow', () => {
  it('renders the title as plain text (no link) when there is no seerr_request_url', () => {
    const wrapper = mountRow({ item: baseItem({ seerr_request_url: '' }) })
    expect(wrapper.find('a').exists()).toBe(false)
    expect(wrapper.text()).toContain('Inception')
  })

  it('renders the title as a link with the safe URL when seerr_request_url is a normal https URL', () => {
    const wrapper = mountRow({ item: baseItem({ seerr_request_url: 'https://seerr.example.com/movie/1' }) })
    const link = wrapper.find('a')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toBe('https://seerr.example.com/movie/1')
    expect(link.attributes('target')).toBe('_blank')
  })

  it('neutralizes a javascript: seerr_request_url to "#" instead of leaving it live', () => {
    const wrapper = mountRow({ item: baseItem({ seerr_request_url: 'javascript:alert(1)' }) })
    const link = wrapper.find('a')
    expect(link.attributes('href')).toBe('#')
  })

  it('renders the requester name as a link built from seerrExternalUrl + user id, sanitized', () => {
    const wrapper = mountRow({
      item: baseItem({ seerr_user_id: 42, seerr_username: 'alice' }),
      seerrExternalUrl: 'https://seerr.example.com',
    })
    const links = wrapper.findAll('a')
    const requesterLink = links.find(l => l.text() === 'alice')
    expect(requesterLink.attributes('href')).toBe('https://seerr.example.com/users/42')
  })

  it('neutralizes a malicious seerrExternalUrl so the requester link cannot execute script', () => {
    const wrapper = mountRow({
      item: baseItem({ seerr_user_id: 42, seerr_username: 'alice' }),
      seerrExternalUrl: 'javascript:void(0)//',
    })
    const links = wrapper.findAll('a')
    const requesterLink = links.find(l => l.text() === 'alice')
    expect(requesterLink.attributes('href')).toBe('#')
  })

  it('renders the requester as plain text (no link) when seerrExternalUrl is not configured', () => {
    const wrapper = mountRow({ item: baseItem({ seerr_user_id: 42, seerr_username: 'alice' }), seerrExternalUrl: '' })
    const links = wrapper.findAll('a')
    expect(links.find(l => l.text() === 'alice')).toBeUndefined()
    expect(wrapper.text()).toContain('alice')
  })

  it('emits delete with the item when the delete button is clicked (pending status only)', async () => {
    const wrapper = mountRow({ item: baseItem({ status: 'pending' }) })
    const buttons = wrapper.findAll('button')
    await buttons[0].trigger('click')
    expect(wrapper.emitted('delete')[0][0].title).toBe('Inception')
  })

  it('emits ignore with the item when the ignore button is clicked', async () => {
    const wrapper = mountRow({ item: baseItem({ status: 'pending' }) })
    const buttons = wrapper.findAll('button')
    await buttons[1].trigger('click')
    expect(wrapper.emitted('ignore')[0][0].title).toBe('Inception')
  })

  it('hides the delete/ignore actions entirely when status is not "pending"', () => {
    const wrapper = mountRow({ item: baseItem({ status: 'queued' }) })
    expect(wrapper.findAll('button')).toHaveLength(0)
  })

  it('shows "never watched" styling/text when last_played is null and view_count is 0', () => {
    const wrapper = mountRow({ item: baseItem({ last_played: null, view_count: 0 }) })
    expect(wrapper.text()).toContain(i18n.global.t('queue.neverWatched'))
  })

  it('shows the watched-but-unknown-date fallback when view_count > 0 but last_played is null', () => {
    const wrapper = mountRow({ item: baseItem({ last_played: null, view_count: 5 }) })
    expect(wrapper.text()).not.toContain(i18n.global.t('queue.neverWatched'))
  })

  it('applies the serverDisabled opacity class and badge when the item belongs to a disabled server', () => {
    const wrapper = mountRow({ serverDisabled: true, server: { name: 'Emby', type: 'emby' } })
    expect(wrapper.find('tr').classes()).toContain('opacity-50')
    expect(wrapper.text()).toContain('off')
  })
})
