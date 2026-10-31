import { describe, it, expect } from 'vitest'
import { formatBytes } from '../formatBytes'

describe('formatBytes', () => {
  it.each([
    [0, '0 B'], [512, '512 B'], [1024, '1.0 KiB'], [1536, '1.5 KiB'],
    [6.6 * 1024 ** 3, '6.6 GiB'], [2 * 1024 ** 4, '2.0 TiB'],
  ])('formats %s as %s', (n, out) => expect(formatBytes(n)).toBe(out))

  it('treats missing or invalid values as 0 B', () => {
    expect(formatBytes(null)).toBe('0 B')
    expect(formatBytes(undefined)).toBe('0 B')
    expect(formatBytes('abc')).toBe('0 B')
  })
})
