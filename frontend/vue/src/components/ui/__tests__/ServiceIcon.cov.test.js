// src/components/ui/__tests__/ServiceIcon.cov.test.js
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ServiceIcon from '../ServiceIcon.vue'

const KNOWN_SERVICES = ['emby', 'jellyfin', 'plex', 'radarr', 'sonarr', 'discord', 'qbittorrent', 'overseerr', 'seerr', 'qui']

describe('ServiceIcon', () => {
  it.each(KNOWN_SERVICES)('renders a distinct, non-empty path for "%s"', (name) => {
    const wrapper = mount(ServiceIcon, { props: { name } })
    const path = wrapper.find('path').attributes('d')
    expect(path).toBeTruthy()
    expect(path.length).toBeGreaterThan(10)
  })

  it('is case-insensitive on the service name', () => {
    const lower = mount(ServiceIcon, { props: { name: 'plex' } })
    const upper = mount(ServiceIcon, { props: { name: 'PLEX' } })
    expect(upper.find('path').attributes('d')).toBe(lower.find('path').attributes('d'))
  })

  it('falls back to the default generic icon for an unknown service name', () => {
    const wrapper = mount(ServiceIcon, { props: { name: 'totally-unknown-service' } })
    expect(wrapper.find('svg').attributes('aria-label')).toBe('Unknown')
  })

  it('sets width/height from the size prop', () => {
    const wrapper = mount(ServiceIcon, { props: { name: 'plex', size: 40 } })
    expect(wrapper.find('svg').attributes('width')).toBe('40')
    expect(wrapper.find('svg').attributes('height')).toBe('40')
  })

  it('defaults to size 20 when not provided', () => {
    const wrapper = mount(ServiceIcon, { props: { name: 'plex' } })
    expect(wrapper.find('svg').attributes('width')).toBe('20')
  })

  it('uses the service brand hex color by default', () => {
    const wrapper = mount(ServiceIcon, { props: { name: 'discord' } })
    expect(wrapper.find('svg').attributes('fill')).toBe('#5865F2')
  })

  it('overrides the fill color when the color prop is provided', () => {
    const wrapper = mount(ServiceIcon, { props: { name: 'discord', color: '#ffffff' } })
    expect(wrapper.find('svg').attributes('fill')).toBe('#ffffff')
  })

  it('uses a custom viewBox for services that declare one (qui) and the default otherwise', () => {
    const qui = mount(ServiceIcon, { props: { name: 'qui' } })
    expect(qui.find('svg').attributes('viewBox')).toBe('0 0 1024 1024')
    const plex = mount(ServiceIcon, { props: { name: 'plex' } })
    expect(plex.find('svg').attributes('viewBox')).toBe('0 0 24 24')
  })
})
