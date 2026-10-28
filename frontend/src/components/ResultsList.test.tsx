import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { makeRecommendation, makeResponse } from '../test/fixtures'
import type { RecommendationRequest } from '../types'
import { ResultsList } from './ResultsList'

const request: RecommendationRequest = { location: 'Indiranagar', cuisines: ['Italian'], min_rating: 4, top_k: 5 }

describe('ResultsList', () => {
  it('renders cards in rank order', () => {
    const response = makeResponse({
      recommendations: [makeRecommendation(3), makeRecommendation(1), makeRecommendation(2)],
    })
    render(<ResultsList response={response} request={{ ...request, top_k: 3 }} />)

    const names = screen.getAllByRole('heading', { level: 3 }).map((heading) => heading.textContent)
    expect(names).toEqual(['Restaurant 1', 'Restaurant 2', 'Restaurant 3'])
  })

  it('renders the empty state for zero results', () => {
    const message = 'No restaurants in BTM match these preferences, even after relaxing the filters.'
    const response = makeResponse({
      source: 'fallback',
      summary: null,
      recommendations: [],
      meta: { candidates_considered: 0, model: null, latency_ms: 3, message },
    })
    render(<ResultsList response={response} request={request} />)

    expect(screen.getByRole('heading', { name: 'No match for these filters' })).toBeInTheDocument()
    expect(screen.getByText(message)).toBeInTheDocument()
    expect(screen.queryAllByRole('article')).toHaveLength(0)
    expect(screen.queryByText('Ranked without AI')).not.toBeInTheDocument()
  })

  it('lets the diner drop a filter instead of inventing matches', async () => {
    const onDropFilter = vi.fn()
    const onEditPreferences = vi.fn()
    const response = makeResponse({ source: 'fallback', summary: null, recommendations: [] })
    render(
      <ResultsList
        response={response}
        request={request}
        onDropFilter={onDropFilter}
        onEditPreferences={onEditPreferences}
      />,
    )

    expect(screen.getByRole('heading', { name: 'No match for these filters' })).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Search without Italian' }))
    expect(onDropFilter).toHaveBeenCalledWith({ ...request, cuisines: [] })
    await userEvent.setup().click(screen.getByRole('button', { name: 'Edit preferences' }))
    expect(onEditPreferences).toHaveBeenCalledTimes(1)
  })

  it('titles the shortlist with the locality and summarises the filters', () => {
    render(<ResultsList response={makeResponse()} request={{ ...request, budget: 'medium' }} />)

    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('Your shortlist · Indiranagar')
    expect(screen.getByText('5 picks · Medium budget · Italian · 4.0+')).toBeInTheDocument()
  })

  it('shows each relaxation as a notice when present', () => {
    const relaxations = ['Lowered minimum rating from 4.8 to 4.3', 'Removed cuisine filter (Mexican)']
    render(<ResultsList response={makeResponse({ relaxations })} request={request} />)

    const notice = within(screen.getByRole('note', { name: 'Search widened' }))
    expect(notice.getByText(relaxations[0])).toBeInTheDocument()
    expect(notice.getByText(relaxations[1])).toBeInTheDocument()
  })

  it('shows no relaxation notice when nothing was relaxed', () => {
    render(<ResultsList response={makeResponse()} request={request} />)
    expect(screen.queryByRole('note', { name: 'Search widened' })).not.toBeInTheDocument()
  })

  it('says how many of the requested results came back when fewer than top_k do', () => {
    const response = makeResponse({ recommendations: [makeRecommendation(1), makeRecommendation(2)] })
    render(<ResultsList response={response} request={{ ...request, top_k: 10 }} />)

    expect(screen.getByText(/^2 of 10 requested · /)).toBeInTheDocument()
    expect(screen.getAllByRole('article')).toHaveLength(2)
  })

  it('leaves out the shortfall when the full top_k comes back', () => {
    render(<ResultsList response={makeResponse()} request={request} />)
    expect(screen.queryByText(/of \d+ requested/)).not.toBeInTheDocument()
  })

  it('shows the fallback notice above fallback results', () => {
    render(<ResultsList response={makeResponse({ source: 'fallback', summary: null })} request={request} />)
    expect(screen.getByText('Ranked without AI')).toBeInTheDocument()
  })
})
