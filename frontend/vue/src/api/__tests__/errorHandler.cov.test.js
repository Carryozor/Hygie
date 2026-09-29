// src/api/__tests__/errorHandler.cov.test.js
// Gap-fill for api/errorHandler.js (71% before this file): the existing
// suite only tests formatApiError()/emitError() directly — the interceptor
// function installErrorInterceptor() actually registers (401 skipped, only
// 422/429/5xx toast, everything else passes through untouched) was never
// invoked. client.test.js mocks axios and only exercises the OTHER
// interceptor (the 401/refresh one) it registers first.
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { installErrorInterceptor, formatApiError } from '../errorHandler'

describe('formatApiError — branch gap fill', () => {
  it('uses the bare message with no field prefix when a Pydantic loc entry is undefined', () => {
    const err = { response: { status: 422, data: { detail: [{ msg: 'field required' }] } } }
    expect(formatApiError(err)).toBe('field required')
  })

  it('uses the bare message with no field prefix when loc only contains "body" (filtered to empty)', () => {
    const err = { response: { status: 422, data: { detail: [{ loc: ['body'], msg: 'invalid' }] } } }
    expect(formatApiError(err)).toBe('invalid')
  })

  it('returns the generic 500 message when the response body has no data at all', () => {
    const err = { response: { status: 500, data: undefined } }
    expect(formatApiError(err)).toBe('Erreur serveur interne')
  })

  it('formats a 403 as "Accès refusé" (not exercised by installErrorInterceptor — 403 is not toasted there)', () => {
    expect(formatApiError({ response: { status: 403, data: {} } })).toBe('Accès refusé')
  })
})

function makeAxiosInstance() {
  const handlers = []
  return {
    interceptors: { response: { use: (succ, err) => handlers.push({ succ, err }) } },
    __handlers: handlers,
  }
}

describe('installErrorInterceptor', () => {
  let events, listener
  beforeEach(() => {
    events = []
    listener = e => events.push(e.detail)
    window.addEventListener('hygie:error', listener)
  })
  afterEach(() => window.removeEventListener('hygie:error', listener))

  it('registers exactly one response interceptor', () => {
    const instance = makeAxiosInstance()
    installErrorInterceptor(instance)
    expect(instance.__handlers.length).toBe(1)
  })

  it('the success handler passes a successful response through unchanged', () => {
    const instance = makeAxiosInstance()
    installErrorInterceptor(instance)
    const response = { status: 200, data: { ok: true } }
    expect(instance.__handlers[0].succ(response)).toBe(response)
  })

  it('re-rejects a 401 without toasting — the auth interceptor owns that status', async () => {
    const instance = makeAxiosInstance()
    installErrorInterceptor(instance)
    const err = { response: { status: 401, data: {} } }
    await expect(instance.__handlers[0].err(err)).rejects.toBe(err)
    expect(events.length).toBe(0)
  })

  it('toasts and re-rejects a 422', async () => {
    const instance = makeAxiosInstance()
    installErrorInterceptor(instance)
    const err = { response: { status: 422, data: { detail: 'Champ requis' } } }
    await expect(instance.__handlers[0].err(err)).rejects.toBe(err)
    expect(events.length).toBe(1)
    expect(events[0].message).toBe('Champ requis')
  })

  it('toasts and re-rejects a 429', async () => {
    const instance = makeAxiosInstance()
    installErrorInterceptor(instance)
    const err = { response: { status: 429, data: {} } }
    await expect(instance.__handlers[0].err(err)).rejects.toBe(err)
    expect(events.length).toBe(1)
  })

  it('toasts and re-rejects a 500', async () => {
    const instance = makeAxiosInstance()
    installErrorInterceptor(instance)
    const err = { response: { status: 500, data: {} } }
    await expect(instance.__handlers[0].err(err)).rejects.toBe(err)
    expect(events.length).toBe(1)
  })

  it('toasts and re-rejects a 599 (upper edge of the 5xx range)', async () => {
    const instance = makeAxiosInstance()
    installErrorInterceptor(instance)
    const err = { response: { status: 599, data: {} } }
    await expect(instance.__handlers[0].err(err)).rejects.toBe(err)
    expect(events.length).toBe(1)
  })

  it('does NOT toast a 403 or 404 — those are left for the caller (useApiAction) to surface', async () => {
    const instance = makeAxiosInstance()
    installErrorInterceptor(instance)
    await expect(instance.__handlers[0].err({ response: { status: 403, data: {} } })).rejects.toBeTruthy()
    await expect(instance.__handlers[0].err({ response: { status: 404, data: {} } })).rejects.toBeTruthy()
    expect(events.length).toBe(0)
  })

  it('does NOT toast a network error (no response at all) — 422/429/5xx check requires a status', async () => {
    const instance = makeAxiosInstance()
    installErrorInterceptor(instance)
    await expect(instance.__handlers[0].err({ response: undefined })).rejects.toBeTruthy()
    expect(events.length).toBe(0)
  })
})
