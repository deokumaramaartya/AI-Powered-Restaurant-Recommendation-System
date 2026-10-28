import { describeFilters } from '../format'
import type { RecommendationRequest, RecommendationResponse } from '../types'
import { EmptyState } from './EmptyState'
import { RestaurantCard } from './RestaurantCard'
import { SummaryBanner } from './SummaryBanner'

interface Props {
  response: RecommendationResponse
  request: RecommendationRequest
  onDropFilter?: (request: RecommendationRequest) => void
  onEditPreferences?: () => void
}

const DEFAULT_TOP_K = 5

export function ResultsList({ response, request, onDropFilter, onEditPreferences }: Props) {
  const { recommendations, relaxations } = response
  const topK = request.top_k ?? DEFAULT_TOP_K

  if (recommendations.length === 0) {
    return (
      <EmptyState
        message={response.meta?.message}
        request={request}
        onDropFilter={onDropFilter}
        onEditPreferences={onEditPreferences}
      />
    )
  }

  const ordered = [...recommendations].sort((a, b) => a.rank - b.rank)
  const count = ordered.length
  const countText = count < topK ? `${count} of ${topK} requested` : count === 1 ? '1 pick' : `${count} picks`

  return (
    <section className="results" aria-labelledby="results-heading">
      <header className="results__header">
        <h2 id="results-heading" className="results__title">
          Your shortlist · <span className="results__place">{request.location}</span>
        </h2>
        <p className="results__query">{[countText, ...describeFilters(request)].join(' · ')}</p>
      </header>

      <SummaryBanner source={response.source} summary={response.summary} />

      {relaxations.length > 0 ? (
        <aside className="widened" role="note" aria-label="Search widened">
          <p className="widened__label">Widened search</p>
          <ul className="widened__list">
            {relaxations.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ul>
        </aside>
      ) : null}

      <ol className="results__list">
        {ordered.map((recommendation) => (
          <li key={recommendation.restaurant.id}>
            <RestaurantCard recommendation={recommendation} />
          </li>
        ))}
      </ol>
    </section>
  )
}
