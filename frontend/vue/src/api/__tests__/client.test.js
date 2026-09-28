// src/api/__tests__/client.test.js
//
// api/client.js owns the 401 -> silent-refresh -> retry flow: the access
// token lives in memory only (see api/tokenStore.js) and a 401 should
// trigger exactly one POST /auth/refresh even when several requests 401 at
// once, replaying every queued request with the new token. A failed refresh
// must log the user out. Public pages (login/setup/the public calendar
// slug) must never trigger the refresh dance at all.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const mocks = vi.hoisted(() => {
  const axiosPost = vi.fn()
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

  return { axiosPost, makeInstance, instances }
})

vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => mocks.makeInstance()),
    post: mocks.axiosPost,
  },
}))

import { getToken, setToken, clearToken } from '@/api/tokenStore'
// Static import: client.js's module-level `axios.create(...)` runs once,
// registering interceptors on mocks.instances[0].
import '../client'

const instance   = mocks.instances[0]
const errHandler = instance.__resHandlers[0].err // the 401/refresh handler (registered before installErrorInterceptor's toast handler)

function make401Error(url) {
  return { config: { url, headers: {} }, response: { status: 401, data: {} } }
}

describe('api/client 401 refresh flow', () => {
  const originalPath = window.location.pathname

  beforeEach(() => {
    instance.mockClear()
    mocks.axiosPost.mockReset()
    localStorage.clear()
    clearToken()
    window.history.pushState({}, '', originalPath)
  })
  afterEach(() => {
    window.history.pushState({}, '', originalPath)
  })

  it('two concurrent 401s trigger exactly one POST /auth/refresh and both requests are retried', async () => {
    mocks.axiosPost.mockResolvedValue({ data: { access_token: 'fresh-token' } })

    const p1 = errHandler(make401Error('/media'))
    const p2 = errHandler(make401Error('/queue'))
    await Promise.all([p1, p2])

    expect(mocks.axiosPost).toHaveBeenCalledTimes(1)
    expect(mocks.axiosPost).toHaveBeenCalledWith('/api/auth/refresh', expect.any(Object))
    expect(instance).toHaveBeenCalledTimes(2)
    expect(getToken()).toBe('fresh-token')
  })

  it('retried requests carry the new Authorization header and are marked _retried', async () => {
    mocks.axiosPost.mockResolvedValue({ data: { access_token: 'fresh-token' } })

    const err = make401Error('/media')
    await errHandler(err)

    expect(err.config._retried).toBe(true)
    expect(err.config.headers.Authorization).toBe('Bearer fresh-token')
    expect(instance).toHaveBeenCalledWith(err.config)
  })

  it('refresh failure clears the token and dispatches hygie:unauthorized', async () => {
    mocks.axiosPost.mockRejectedValue({ response: { status: 401 } })
    setToken('stale-token')

    const events = []
    const listener = e => events.push(e)
    window.addEventListener('hygie:unauthorized', listener)

    await expect(errHandler(make401Error('/media'))).rejects.toBeTruthy()

    window.removeEventListener('hygie:unauthorized', listener)
    expect(events.length).toBe(1)
    expect(getToken()).toBe('')
    expect(instance).not.toHaveBeenCalled() // no retry on a failed refresh
  })

  it('a second 401 queued during a failing refresh also rejects (does not hang)', async () => {
    mocks.axiosPost.mockRejectedValue({ response: { status: 401 } })

    const p1 = errHandler(make401Error('/media'))
    const p2 = errHandler(make401Error('/queue'))

    await expect(p1).rejects.toBeTruthy()
    await expect(p2).rejects.toBeTruthy()
    expect(mocks.axiosPost).toHaveBeenCalledTimes(1)
  })

  describe('public-page detection', () => {
    it('does not attempt a refresh on the public calendar slug (single-segment, unknown route)', async () => {
      window.history.pushState({}, '', '/my-public-calendar')

      await expect(errHandler(make401Error('/media'))).rejects.toBeTruthy()

      expect(mocks.axiosPost).not.toHaveBeenCalled()
    })

    it('does not attempt a refresh on /login', async () => {
      window.history.pushState({}, '', '/login')

      await expect(errHandler(make401Error('/auth/me'))).rejects.toBeTruthy()

      expect(mocks.axiosPost).not.toHaveBeenCalled()
    })

    it('does not attempt a refresh on /setup', async () => {
      window.history.pushState({}, '', '/setup')

      await expect(errHandler(make401Error('/media'))).rejects.toBeTruthy()

      expect(mocks.axiosPost).not.toHaveBeenCalled()
    })

    it('DOES attempt a refresh on the protected dashboard route "/"', async () => {
      window.history.pushState({}, '', '/')
      mocks.axiosPost.mockResolvedValue({ data: { access_token: 't' } })

      await errHandler(make401Error('/media'))

      expect(mocks.axiosPost).toHaveBeenCalledTimes(1)
    })

    it('DOES attempt a refresh on a protected multi-segment route (/library/42)', async () => {
      window.history.pushState({}, '', '/library/42')
      mocks.axiosPost.mockResolvedValue({ data: { access_token: 't' } })

      await errHandler(make401Error('/media'))

      expect(mocks.axiosPost).toHaveBeenCalledTimes(1)
    })
  })
})
