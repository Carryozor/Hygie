// src/components/ui/__tests__/HygieLogoSvg.cov.test.js
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import HygieLogoSvg from '../HygieLogoSvg.vue'

describe('HygieLogoSvg', () => {
  it('sizes the svg as size + 2*padding', () => {
    const wrapper = mount(HygieLogoSvg, { props: { size: 32 } })
    // pad=9 -> total = 32 + 18 = 50
    expect(wrapper.find('svg').attributes('width')).toBe('50')
    expect(wrapper.find('svg').attributes('height')).toBe('50')
  })

  it('renders a single dim arc when no servers are configured', () => {
    const wrapper = mount(HygieLogoSvg, { props: { serverResults: [] } })
    const circles = wrapper.findAll('circle')
    expect(circles).toHaveLength(1)
    expect(circles[0].attributes('stroke-opacity')).toBe('0.3')
  })

  it('renders one arc per configured server (up to 3)', () => {
    const wrapper = mount(HygieLogoSvg, {
      props: { serverResults: [{ ok: true, type: 'plex' }, { ok: true, type: 'emby' }, { ok: false, type: 'jellyfin' }] },
    })
    expect(wrapper.findAll('circle')).toHaveLength(3)
  })

  it('colors a healthy server arc by its brand color', () => {
    const wrapper = mount(HygieLogoSvg, { props: { serverResults: [{ ok: true, type: 'plex' }] } })
    expect(wrapper.find('circle').attributes('stroke')).toBe('#E5A00D')
  })

  it('dims an unhealthy (ok:false) server arc instead of using its brand color', () => {
    const wrapper = mount(HygieLogoSvg, { props: { serverResults: [{ ok: false, type: 'plex' }] } })
    const circle = wrapper.find('circle')
    expect(circle.attributes('stroke')).toBe('#1f7d8c')
    expect(circle.attributes('stroke-opacity')).toBe('0.35')
  })

  it('renders every arc red and applies the blinking error class when hasError is true, overriding per-server health', () => {
    const wrapper = mount(HygieLogoSvg, {
      props: { hasError: true, serverResults: [{ ok: true, type: 'plex' }, { ok: false, type: 'emby' }] },
    })
    const circles = wrapper.findAll('circle')
    for (const c of circles) {
      expect(c.attributes('stroke')).toBe('#ef4444')
      expect(c.classes()).toContain('arc-error')
    }
  })

  it('falls back to the generic blue-ish color for an unrecognized server type', () => {
    const wrapper = mount(HygieLogoSvg, { props: { serverResults: [{ ok: true, type: 'unknown-type' }] } })
    expect(wrapper.find('circle').attributes('stroke')).toBe('#22c1d6')
  })

  it('embeds the logo image centered on the svg', () => {
    const wrapper = mount(HygieLogoSvg, { props: { size: 32 } })
    const image = wrapper.find('image')
    expect(image.attributes('width')).toBe('32')
    expect(image.attributes('height')).toBe('32')
  })
})
