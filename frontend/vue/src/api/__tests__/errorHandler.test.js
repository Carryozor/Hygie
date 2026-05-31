// src/api/__tests__/errorHandler.test.js
import { describe, it, expect } from 'vitest'
import { formatApiError, emitError } from '../errorHandler'

describe('formatApiError', () => {
  it('returns network error when no response', () => {
    expect(formatApiError({ response: undefined })).toContain('réseau')
  })

  it('formats single 422 string detail', () => {
    const err = { response: { status: 422, data: { detail: 'Champ requis' } } }
    expect(formatApiError(err)).toBe('Champ requis')
  })

  it('formats array of 422 Pydantic errors', () => {
    const err = {
      response: {
        status: 422,
        data: {
          detail: [
            { loc: ['body', 'name'], msg: 'field required' },
            { loc: ['body', 'url'],  msg: 'invalid url' },
          ],
        },
      },
    }
    const result = formatApiError(err)
    expect(result).toContain('name')
    expect(result).toContain('field required')
    expect(result).toContain('url')
  })

  it('filters "body" from loc path', () => {
    const err = {
      response: {
        status: 422,
        data: { detail: [{ loc: ['body', 'email'], msg: 'invalid' }] },
      },
    }
    const r = formatApiError(err)
    expect(r).not.toContain('body')
    expect(r).toContain('email')
  })

  it('returns server error for 500 without detail', () => {
    expect(formatApiError({ response: { status: 500, data: {} } }))
      .toBe('Erreur serveur interne')
  })

  it('uses detail field for 500', () => {
    expect(formatApiError({ response: { status: 500, data: { detail: 'DB down' } } }))
      .toBe('DB down')
  })

  it('returns introuvable for 404', () => {
    expect(formatApiError({ response: { status: 404, data: {} } }))
      .toContain('introuvable')
  })

  it('returns generic error for unknown status', () => {
    expect(formatApiError({ response: { status: 418, data: {} } }))
      .toContain('418')
  })
})

describe('emitError', () => {
  it('dispatches hygie:error CustomEvent on window', () => {
    const events = []
    window.addEventListener('hygie:error', e => events.push(e.detail))
    emitError('Test error', 'warning')
    expect(events.length).toBeGreaterThan(0)
    const last = events.at(-1)
    expect(last.message).toBe('Test error')
    expect(last.type).toBe('warning')
  })

  it('defaults type to error', () => {
    const events = []
    window.addEventListener('hygie:error', e => events.push(e.detail))
    emitError('Default')
    expect(events.at(-1).type).toBe('error')
  })
})
