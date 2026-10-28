import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { jsonResponse, makeResponse, sampleMeta } from './test/fixtures'

type Handler = (init?: RequestInit) => Promise<Response>

function routeFetch(routes: { meta?: Handler; recommendations?: Handler }) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    if (url.endsWith('/api/meta')) return (routes.meta ?? (async () => jsonResponse(sampleMeta)))(init)
    if (url.endsWith('/api/recommendations') && routes.recommendations) return routes.recommendations(init)
    throw new Error(`unexpected request to ${url}`)
  })
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

function hangUntilAborted(init?: RequestInit): Promise<Response> {
  return new Promise((_resolve, reject) => {
    init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
  })
}

async function chooseLocationAndSubmit(user: ReturnType<typeof userEvent.setup>, location = 'Indiranagar') {
  await user.selectOptions(await screen.findByLabelText('Locality'), location)
  await user.click(screen.getByRole('button', { name: 'Find restaurants' }))
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('App', () => {
  it('loads meta, shows a loading state, then ranked cards', async () => {
    const pending = deferred<Response>()
    routeFetch({ recommendations: () => pending.promise })
    const user = userEvent.setup()
    render(<App />)

    await chooseLocationAndSubmit(user)
    expect(screen.getAllByTestId('skeleton-card').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /finding restaurants/i })).toBeDisabled()

    await act(async () => pending.resolve(jsonResponse(makeResponse())))

    expect(await screen.findAllByRole('article')).toHaveLength(5)
    expect(screen.queryByTestId('skeleton-card')).not.toBeInTheDocument()
    expect(screen.getByLabelText('AI summary')).toHaveTextContent('Strong Italian picks with table booking.')
    expect(screen.getByText('5 restaurants found in Indiranagar')).toBeInTheDocument()
  })

  it('shows a retry button when meta fails, and loads the form after retrying', async () => {
    let calls = 0
    routeFetch({
      meta: async () => {
        calls += 1
        if (calls === 1) throw new TypeError('Failed to fetch')
        return jsonResponse(sampleMeta)
      },
    })
    const user = userEvent.setup()
    render(<App />)

    expect(await screen.findByText("Can't reach the server")).toBeInTheDocument()
    expect(screen.queryByLabelText('Locality')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByLabelText('Locality')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('tells a 503 apart from an unreachable server', async () => {
    routeFetch({
      meta: async () =>
        jsonResponse(
          { detail: 'Dataset not loaded. Run `python -m scripts.ingest` from backend/ and restart the server.' },
          503,
        ),
    })
    render(<App />)

    expect(await screen.findByText('Data not loaded, run ingestion')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('python -m scripts.ingest')
    expect(screen.queryByText("Can't reach the server")).not.toBeInTheDocument()
  })

  it('explains that ingestion is needed when meta has no localities', async () => {
    routeFetch({ meta: async () => jsonResponse({ ...sampleMeta, locations: [], cuisines: [] }) })
    render(<App />)

    expect(await screen.findByText('The restaurant list is empty')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('python -m scripts.ingest')
    expect(screen.queryByLabelText('Locality')).not.toBeInTheDocument()
  })

  it('shows a generic message for other server errors', async () => {
    routeFetch({ recommendations: async () => new Response('boom', { status: 500 }) })
    const user = userEvent.setup()
    render(<App />)

    await chooseLocationAndSubmit(user)
    expect(await screen.findByText('Something went wrong, try again')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('asks for a page reload when the chosen locality no longer exists', async () => {
    routeFetch({
      recommendations: async () =>
        jsonResponse({ detail: [{ loc: ['body', 'location'], msg: "Unknown location 'BTM'." }] }, 422),
    })
    const user = userEvent.setup()
    render(<App />)

    await chooseLocationAndSubmit(user, 'BTM')
    expect(await screen.findByText("Some preferences weren't accepted")).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Reload the page')
    expect(screen.getByRole('button', { name: 'Reload page' })).toBeInTheDocument()
  })

  it('shows a 422 message next to the field it belongs to', async () => {
    routeFetch({
      recommendations: async () =>
        jsonResponse(
          {
            detail: [
              {
                loc: ['body', 'min_rating'],
                field: 'min_rating',
                msg: 'Minimum rating must be a number from 0 to 5.',
                type: 'less_than_equal',
              },
            ],
          },
          422,
        ),
    })
    const user = userEvent.setup()
    render(<App />)

    await chooseLocationAndSubmit(user)
    expect(await screen.findByRole('alert')).toHaveTextContent('Check the highlighted fields in the form.')
    expect(screen.getByLabelText('Minimum rating')).toHaveAccessibleDescription(
      'Minimum rating must be a number from 0 to 5.',
    )
    expect(screen.queryByRole('button', { name: 'Reload page' })).not.toBeInTheDocument()
  })

  it('does not start a second search while one is in flight', async () => {
    const fetchSpy = routeFetch({ recommendations: hangUntilAborted })
    const user = userEvent.setup()
    render(<App />)

    await chooseLocationAndSubmit(user)
    await user.type(screen.getByLabelText(/anything else/i), 'quiet{Enter}')
    await user.click(screen.getByRole('button', { name: /finding restaurants/i }))

    const searches = fetchSpy.mock.calls.filter(([url]) => String(url).endsWith('/api/recommendations'))
    expect(searches).toHaveLength(1)
  })

  it('gives up after 20 seconds and offers a retry', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const fetchSpy = routeFetch({ recommendations: hangUntilAborted })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    render(<App />)

    await chooseLocationAndSubmit(user)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(19_000)
    })
    expect(screen.getAllByTestId('skeleton-card').length).toBeGreaterThan(0)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_500)
    })
    expect(await screen.findByText('The server took too long to respond')).toBeInTheDocument()
    expect(screen.queryByTestId('skeleton-card')).not.toBeInTheDocument()

    fetchSpy.mockImplementation(async (input) =>
      String(input).endsWith('/api/meta') ? jsonResponse(sampleMeta) : jsonResponse(makeResponse()),
    )
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(screen.getAllByRole('article')).toHaveLength(5))
  })

  it('keeps results on screen while the form is edited', async () => {
    routeFetch({ recommendations: async () => jsonResponse(makeResponse()) })
    const user = userEvent.setup()
    render(<App />)

    await chooseLocationAndSubmit(user)
    expect(await screen.findAllByRole('article')).toHaveLength(5)

    await user.selectOptions(screen.getByLabelText('Locality'), 'HSR')
    await user.click(screen.getByRole('checkbox', { name: 'Chinese' }))
    expect(screen.getAllByRole('article')).toHaveLength(5)
  })

  it('collapses the form behind an Edit bar after submit on a narrow screen', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query.includes('max-width: 820px'),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
      onchange: null,
    }))
    routeFetch({ recommendations: async () => jsonResponse(makeResponse()) })
    const user = userEvent.setup()
    render(<App />)

    await chooseLocationAndSubmit(user)
    expect(await screen.findAllByRole('article')).toHaveLength(5)
    expect(screen.getByLabelText('Locality')).not.toBeVisible()
    expect(screen.getByRole('button', { name: 'Edit preferences' })).toBeVisible()

    await user.click(screen.getByRole('button', { name: 'Edit preferences' }))
    expect(screen.getByLabelText('Locality')).toBeVisible()
    expect(screen.getAllByRole('article')).toHaveLength(5)
  })
})
