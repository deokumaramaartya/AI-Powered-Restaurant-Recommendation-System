import type { RecommendationResponse } from '../types'

interface Props {
  source: RecommendationResponse['source']
  summary: string | null
}

export function SummaryBanner({ source, summary }: Props) {
  if (source === 'fallback') {
    return (
      <section className="fallback" role="status" aria-label="Ranked without AI">
        <span className="fallback__icon" aria-hidden="true">
          !
        </span>
        <p className="fallback__title">Ranked without AI</p>
        <p className="fallback__body">
          Our AI ranker isn’t responding right now, so these are sorted by rating and votes. The “Why it fits” notes
          come straight from your filters.
        </p>
      </section>
    )
  }
  if (!summary?.trim()) {
    return null
  }
  return (
    <section className="chit" aria-label="AI summary">
      <div className="chit__header">
        <p className="chit__label">Tonight’s read</p>
        <p className="chit__source">AI-ranked by Groq</p>
      </div>
      <p className="chit__body">{summary}</p>
    </section>
  )
}
