import { describe, it, expect } from 'vitest'
import { remainingBucket } from '@/utils/timeRemaining'

// Fixed reference instant so tests are deterministic.
const NOW = new Date('2026-07-08T12:00:00Z')
const inMs = (ms) => new Date(NOW.getTime() + ms).toISOString()
const H = 3600 * 1000
const D = 24 * H

describe('remainingBucket', () => {
  it('returns none when deleteAt is missing', () => {
    expect(remainingBucket(null, NOW)).toEqual({ kind: 'none' })
    expect(remainingBucket('', NOW)).toEqual({ kind: 'none' })
  })

  it('returns exceeded when the delete instant is in the past', () => {
    expect(remainingBucket(inMs(-H), NOW)).toEqual({ kind: 'exceeded' })
  })

  it('reports hours (not "tomorrow") when deletion is one hour away', () => {
    // This is the reported bug: 1h left must NOT render as "Demain".
    expect(remainingBucket(inMs(1 * H), NOW)).toEqual({ kind: 'hours', value: 1 })
  })

  it('reports hours when deletion is less than a day away', () => {
    expect(remainingBucket(inMs(23 * H), NOW)).toEqual({ kind: 'hours', value: 23 })
  })

  it('reports soon when less than an hour remains', () => {
    expect(remainingBucket(inMs(30 * 60 * 1000), NOW)).toEqual({ kind: 'soon' })
  })

  it('treats exactly 24h as tomorrow (days bucket, value 1)', () => {
    expect(remainingBucket(inMs(1 * D), NOW)).toEqual({ kind: 'days', value: 1 })
  })

  it('reports whole days beyond one day', () => {
    expect(remainingBucket(inMs(25 * H), NOW)).toEqual({ kind: 'days', value: 2 })
    expect(remainingBucket(inMs(5 * D), NOW)).toEqual({ kind: 'days', value: 5 })
  })

  it('never emits 24 in the hours bucket (rolls into days)', () => {
    const b = remainingBucket(inMs(23.8 * H), NOW)
    expect(b.kind).toBe('hours')
    expect(b.value).toBeLessThanOrEqual(23)
    expect(b.value).toBeGreaterThanOrEqual(1)
  })
})
