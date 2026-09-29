// src/api/__tests__/client.cov.test.js
// Gap-fill for api/client.js (91% before this file): the request
// interceptor (injects Bearer token) and the response interceptor's
// success passthrough (`r => r`) were never directly invoked by
// client.test.js, which only drives the 401/error handler.
import { describe, it, expect, vi, beforeEach } from 'vitest'

const mocks = vi.hoisted(() => {
  const instances = []
  function makeInstance() {
    const reqHandlers = []
    const resHandlers = []
    const instance = vi.fn(config => Promise.resolve({ data: {}, status: 200, config }))
    instance.interceptors = {
      request:  { use: h => reqHandlers.push(h) },
      response: { use: (succ, err) => resHandlers.push({ succ, err }) },
    }
    instance.defaults = { headers: { common: {} } }
    instance.__reqHandlers = reqHandlers
    instance.__resHandlers = resHandlers
    instances.push(instance)
    return instance
  }
  return { makeInstance, instances }
})

const axiosPost = vi.hoisted(() => vi.fn())

vi.mock('axios', () => ({
  default: { create: vi.fn(() => mocks.makeInstance()), post: axiosPost },
}))

import { getToken, setToken, clearToken } from '@/api/tokenStore'
import '../client'

const instance = mocks.instances[0]
const reqHandler = instance.__reqHandlers[0]
const successHandler = instance.__resHandlers[0].succ
const errHandler = instance.__resHandlers[0].err

describe('api/client — request interceptor and success passthrough', () => {
  beforeEach(() => clearToken())

  it('adds no Authorization header when there is no token', () => {
    const cfg = reqHandler({ headers: {} })
    expect(cfg.headers.Authorization).toBeUndefined()
  })

  it('injects "Bearer <token>" into the Authorization header when a token is present', () => {
    setToken('my-access-token')
    const cfg = reqHandler({ headers: {} })
    expect(cfg.headers.Authorization).toBe('Bearer my-access-token')
  })

  it('returns the same config object it was given (does not clone it)', () => {
    const cfg = { headers: {} }
    expect(reqHandler(cfg)).toBe(cfg)
  })

  it('the response success handler passes a successful response straight through unchanged', () => {
    const response = { status: 200, data: { hello: 'world' } }
    expect(successHandler(response)).toBe(response)
  })

  it('the 401 refresh flow falls back to the legacy "token" field when access_token is absent', async () => {
    instance.mockClear()
    axiosPost.mockReset().mockResolvedValue({ data: { token: 'legacy-refreshed' } })
    const err = { config: { url: '/media', headers: {} }, response: { status: 401, data: {} } }
    await errHandler(err)
    expect(getToken()).toBe('legacy-refreshed')
    expect(err.config.headers.Authorization).toBe('Bearer legacy-refreshed')
  })
})
