import { afterEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, makeResponse, sampleMeta } from '../test/fixtures'
import { ApiError, getMeta, getRecommendations, isAbortError, resolveApiBase } from './client'

function mockFetch(implementation: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(implementation)
}

async function captureError(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise
  } catch (error) {
    return error
  }
  throw new Error('expected the request to fail')
}

afterEach(() => {
  vi.useRealTimers()
})

describe('api client', () => {
  it('resolveApiBase strips trailing slashes and whitespace', () => {
    expect(resolveApiBase(undefined)).toBe('')
    expect(resolveApiBase('')).toBe('')
    expect(resolveApiBase('https://api.example.com')).toBe('https://api.example.com')
    expect(resolveApiBase('https://api.example.com/')).toBe('https://api.example.com')
    expect(resolveApiBase('  https://api.example.com///  ')).toBe('https://api.example.com')
  })

  it('getMeta returns the parsed body', async () => {
    const fetchSpy = mockFetch(async () => jsonResponse(sampleMeta))
    await expect(getMeta()).resolves.toEqual(sampleMeta)
    expect(fetchSpy.mock.calls[0][0]).toBe('/api/meta')
  })

  it('getRecommendations posts the request as JSON', async () => {
    const fetchSpy = mockFetch(async () => jsonResponse(makeResponse()))
    await getRecommendations({ location: 'BTM', top_k: 5 })

    const [url, init] = fetchSpy.mock.calls[0]
    expect(url).toBe('/api/recommendations')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({ location: 'BTM', top_k: 5 })
  })

  it('reports a network failure as unreachable', async () => {
    mockFetch(async () => {
      throw new TypeError('Failed to fetch')
    })
    const error = await captureError(getMeta())
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).kind).toBe('network')
  })

  it('reports a 502 from the dev proxy as unreachable', async () => {
    mockFetch(async () => new Response('', { status: 502 }))
    const error = (await captureError(getMeta())) as ApiError
    expect(error.kind).toBe('network')
  })

  it('reports a 503 as data not loaded, with the backend detail', async () => {
    const detail = 'Dataset not loaded. Run `python -m scripts.ingest` from backend/ and restart the server.'
    mockFetch(async () => jsonResponse({ detail }, 503))
    const error = (await captureError(getMeta())) as ApiError
    expect(error.kind).toBe('unavailable')
    expect(error.message).toBe(detail)
  })

  it('reports other 5xx responses as a server error', async () => {
    mockFetch(async () => new Response('Internal Server Error', { status: 500 }))
    const error = (await captureError(getMeta())) as ApiError
    expect(error.kind).toBe('server')
    expect(error.status).toBe(500)
  })

  it('parses 422 field errors', async () => {
    mockFetch(async () =>
      jsonResponse(
        {
          detail: [
            { type: 'value_error', loc: ['body', 'location'], msg: "Unknown location 'Atlantis'." },
            { type: 'value_error', loc: ['body', 'cuisines'], msg: 'Unknown cuisine(s): Martian.' },
          ],
        },
        422,
      ),
    )
    const error = (await captureError(getRecommendations({ location: 'Atlantis' }))) as ApiError
    expect(error.kind).toBe('validation')
    expect(error.fieldErrors).toEqual([
      { field: 'location', message: "Unknown location 'Atlantis'." },
      { field: 'cuisines', message: 'Unknown cuisine(s): Martian.' },
    ])
  })

  it('prefers the field name the backend sends', async () => {
    mockFetch(async () =>
      jsonResponse(
        {
          detail: [
            { loc: ['body', 'cuisines', 2], field: 'cuisines', msg: 'Cuisines must be a list of at most 20 names.' },
            { loc: ['body', 3], field: 'body', msg: 'JSON decode error' },
          ],
        },
        422,
      ),
    )
    const error = (await captureError(getRecommendations({ location: 'BTM' }))) as ApiError
    expect(error.fieldErrors).toEqual([
      { field: 'cuisines', message: 'Cuisines must be a list of at most 20 names.' },
      { field: 'body', message: 'JSON decode error' },
    ])
  })

  it('gives up after the timeout and reports it', async () => {
    vi.useFakeTimers()
    mockFetch(
      (_input, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
        }),
    )
    const pending = captureError(getRecommendations({ location: 'BTM' }, { timeoutMs: 20_000 }))
    await vi.advanceTimersByTimeAsync(20_000)

    const error = (await pending) as ApiError
    expect(error).toBeInstanceOf(ApiError)
    expect(error.kind).toBe('timeout')
  })

  it('passes a caller abort through as an AbortError', async () => {
    mockFetch(
      (_input, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
        }),
    )
    const controller = new AbortController()
    const pending = captureError(getMeta({ signal: controller.signal }))
    controller.abort()
    expect(isAbortError(await pending)).toBe(true)
  })
})
