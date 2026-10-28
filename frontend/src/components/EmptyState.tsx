import { filterDrops } from '../format'
import type { RecommendationRequest } from '../types'

interface Props {
  message?: string | null
  request: RecommendationRequest
  onDropFilter?: (request: RecommendationRequest) => void
  onEditPreferences?: () => void
}

export function EmptyState({ message, request, onDropFilter, onEditPreferences }: Props) {
  const drops = filterDrops(request)
  return (
    <section className="state" aria-labelledby="empty-heading">
      <p className="state__eyebrow">No matches</p>
      <h2 id="empty-heading" className="state__title">
        No match for these filters
      </h2>
      <p className="state__body">
        {message || 'Nothing in this locality fits all of your preferences. Drop a filter to search again.'}
      </p>
      {drops.length > 0 && onDropFilter ? (
        <>
          <p className="state__subhead">Drop a filter and search again</p>
          <div className="state__actions">
            {drops.map((drop) => (
              <button
                key={drop.id}
                type="button"
                className="button button--outline button--compact"
                onClick={() => onDropFilter(drop.request)}
              >
                {drop.label}
              </button>
            ))}
          </div>
        </>
      ) : null}
      {onEditPreferences ? (
        <button type="button" className="button button--outline" onClick={onEditPreferences}>
          Edit preferences
        </button>
      ) : null}
    </section>
  )
}
