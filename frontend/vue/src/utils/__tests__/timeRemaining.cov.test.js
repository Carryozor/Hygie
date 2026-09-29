// src/utils/__tests__/timeRemaining.cov.test.js
// Gap-fill: an unparsable deleteAt string (malformed data from the backend)
// must degrade to the "none" bucket instead of propagating NaN into the
// day/hour math and rendering a nonsense countdown.
import { describe, it, expect } from 'vitest'
import { remainingBucket } from '@/utils/timeRemaining'

describe('remainingBucket — malformed input', () => {
  it('returns "none" when deleteAt is not a parsable date', () => {
    expect(remainingBucket('not-a-real-date')).toEqual({ kind: 'none' })
  })
})
