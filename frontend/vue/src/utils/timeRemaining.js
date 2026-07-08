// frontend/vue/src/utils/timeRemaining.js
// Pure time-until-deletion bucketing, shared by the queue label/urgency helpers.
// Kept free of i18n/DOM so it is unit-testable and has a single source of truth
// for how "time left" is categorised.

const HOUR_MS = 60 * 60 * 1000
const DAY_MS = 24 * HOUR_MS

/**
 * Classify the time remaining until `deleteAt`.
 *
 * @param {string|Date|null|undefined} deleteAt ISO string or Date of the scheduled deletion.
 * @param {Date} [now] Reference instant (injectable for tests).
 * @returns {{kind: 'none'|'exceeded'|'soon'|'hours'|'days', value?: number}}
 *   - none: no deletion scheduled
 *   - exceeded: the instant is in the past
 *   - soon: less than one hour left (imminent)
 *   - hours: 1..23 hours left (value = whole hours)
 *   - days: one or more whole days left (value = days; 1 == tomorrow)
 */
export function remainingBucket(deleteAt, now = new Date()) {
  if (!deleteAt) return { kind: 'none' }

  const ms = new Date(deleteAt).getTime() - now.getTime()
  if (Number.isNaN(ms)) return { kind: 'none' }
  if (ms <= 0) return { kind: 'exceeded' }
  if (ms < HOUR_MS) return { kind: 'soon' }

  if (ms < DAY_MS) {
    // Whole hours, clamped to 1..23 so we never render "24h" — that rolls into
    // the days bucket below. This is the fix for the last-day case where a
    // deletion an hour away wrongly showed "tomorrow".
    const hours = Math.min(23, Math.max(1, Math.round(ms / HOUR_MS)))
    return { kind: 'hours', value: hours }
  }

  return { kind: 'days', value: Math.ceil(ms / DAY_MS) }
}
