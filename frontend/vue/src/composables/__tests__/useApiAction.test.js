// src/composables/__tests__/useApiAction.test.js
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { runConfirmedAction, reportActionError } from '../useApiAction'

describe('reportActionError', () => {
  let events
  let listener
  beforeEach(() => {
    events = []
    listener = e => events.push(e.detail)
    window.addEventListener('hygie:error', listener)
  })
  afterEach(() => window.removeEventListener('hygie:error', listener))

  it('emits a toast for a 403 (not surfaced by the axios interceptor)', () => {
    reportActionError({ response: { status: 403, data: {} } })
    expect(events.length).toBe(1)
  })

  it('emits a toast for a 404 (not surfaced by the axios interceptor)', () => {
    reportActionError({ response: { status: 404, data: {} } })
    expect(events.length).toBe(1)
  })

  it('does NOT double-toast a 422 (already handled by installErrorInterceptor)', () => {
    reportActionError({ response: { status: 422, data: { detail: 'x' } } })
    expect(events.length).toBe(0)
  })

  it('does NOT double-toast a 429 (already handled by installErrorInterceptor)', () => {
    reportActionError({ response: { status: 429, data: {} } })
    expect(events.length).toBe(0)
  })

  it('does NOT double-toast a 5xx (already handled by installErrorInterceptor)', () => {
    reportActionError({ response: { status: 500, data: {} } })
    expect(events.length).toBe(0)
  })

  it('emits a network-error toast when there is no response at all', () => {
    reportActionError({ response: undefined })
    expect(events.length).toBe(1)
  })

  it('prefixes the toast with the given action label when provided', () => {
    reportActionError({ response: { status: 403, data: {} } }, 'Impossible d\'ignorer ce média')
    expect(events.at(-1).message).toContain('Impossible d\'ignorer ce média')
    expect(events.at(-1).message).toContain('Accès refusé')
  })

  it('omits the label prefix when none is given', () => {
    reportActionError({ response: { status: 403, data: {} } })
    expect(events.at(-1).message).toBe('Accès refusé')
  })
})

describe('runConfirmedAction', () => {
  it('calls onSuccess and reload, and returns true when the action resolves', async () => {
    const action = vi.fn().mockResolvedValue(undefined)
    const onSuccess = vi.fn()
    const reload = vi.fn().mockResolvedValue(undefined)

    const ok = await runConfirmedAction({ action, onSuccess, reload })

    expect(ok).toBe(true)
    expect(action).toHaveBeenCalledTimes(1)
    expect(onSuccess).toHaveBeenCalledTimes(1)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('does NOT call onSuccess when the action rejects — modal/target must stay open', async () => {
    const action = vi.fn().mockRejectedValue({ response: { status: 403, data: {} } })
    const onSuccess = vi.fn()
    const reload = vi.fn().mockResolvedValue(undefined)

    const ok = await runConfirmedAction({ action, onSuccess, reload })

    expect(ok).toBe(false)
    expect(onSuccess).not.toHaveBeenCalled()
  })

  it('emits an error toast on a rejected action with an uncovered status (403)', async () => {
    const events = []
    const listener = e => events.push(e.detail)
    window.addEventListener('hygie:error', listener)
    const action = vi.fn().mockRejectedValue({ response: { status: 403, data: {} } })

    await runConfirmedAction({ action, onSuccess: vi.fn(), reload: vi.fn() })

    window.removeEventListener('hygie:error', listener)
    expect(events.length).toBe(1)
  })

  it('still reloads the list after a rejected action', async () => {
    const action = vi.fn().mockRejectedValue({ response: { status: 404, data: {} } })
    const reload = vi.fn().mockResolvedValue(undefined)

    await runConfirmedAction({ action, reload })

    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('never throws — callers can await it directly from a template handler', async () => {
    const action = vi.fn().mockRejectedValue(new Error('boom'))
    await expect(runConfirmedAction({ action })).resolves.toBe(false)
  })

  it('works without onSuccess/reload callbacks', async () => {
    const action = vi.fn().mockResolvedValue(undefined)
    await expect(runConfirmedAction({ action })).resolves.toBe(true)
  })

  it('passes errorLabel through to the emitted toast', async () => {
    const events = []
    const listener = e => events.push(e.detail)
    window.addEventListener('hygie:error', listener)
    const action = vi.fn().mockRejectedValue({ response: { status: 403, data: {} } })

    await runConfirmedAction({ action, errorLabel: 'Impossible de purger' })

    window.removeEventListener('hygie:error', listener)
    expect(events.at(-1).message).toContain('Impossible de purger')
  })
})
