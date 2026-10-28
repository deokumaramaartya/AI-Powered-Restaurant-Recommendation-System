import type { BudgetTier, RecommendationRequest, TierBounds } from './types'

const indianNumber = new Intl.NumberFormat('en-IN')

export const BUDGET_LABELS: Record<BudgetTier, string> = { low: 'Low', medium: 'Medium', high: 'High' }

export function formatNumber(value: number): string {
  return indianNumber.format(value)
}

export function formatRupees(value: number): string {
  return `₹${indianNumber.format(value)}`
}

export function formatRating(value: number): string {
  return value.toFixed(1)
}

/** Minimum-rating label: "Any" when the floor is 0, otherwise e.g. "4.0+". */
export function formatMinRating(value: number): string {
  return value > 0 ? `${formatRating(value)}+` : 'Any'
}

export function formatRank(rank: number): string {
  return String(rank).padStart(2, '0')
}

export function formatCostForTwo(value: number): string {
  return `${formatRupees(value)} for two`
}

export function formatTierBounds(bounds: TierBounds | undefined): string {
  if (!bounds) return ''
  const { min, max } = bounds
  if (min != null && max != null) return `${formatRupees(min)}–${formatRupees(max)}`
  if (max != null) return `≤${formatRupees(max)}`
  if (min != null) return `${formatRupees(min)}+`
  return ''
}

/** Budget, cuisines, rating floor, and the anything-else note of a submitted search. */
export function describeFilters(request: RecommendationRequest): string[] {
  const parts = [
    request.budget ? `${BUDGET_LABELS[request.budget]} budget` : 'Any budget',
    request.cuisines?.length ? request.cuisines.join(', ') : 'Any cuisine',
    request.min_rating ? formatMinRating(request.min_rating) : 'Any rating',
  ]
  const extra = request.extra_preferences?.trim()
  if (extra) parts.push(extra)
  return parts
}

export interface FilterDrop {
  id: string
  label: string
  request: RecommendationRequest
}

/** Filters the diner can remove. Location is never offered. */
export function filterDrops(request: RecommendationRequest): FilterDrop[] {
  const drops: FilterDrop[] = []
  if (request.cuisines?.length) {
    const label =
      request.cuisines.length === 1 ? `Search without ${request.cuisines[0]}` : 'Search without cuisine'
    drops.push({ id: 'cuisines', label, request: { ...request, cuisines: [] } })
  }
  if (request.budget) {
    drops.push({ id: 'budget', label: 'Search any budget', request: { ...request, budget: undefined } })
  }
  if (request.min_rating && request.min_rating > 0) {
    drops.push({ id: 'rating', label: 'Search any rating', request: { ...request, min_rating: 0 } })
  }
  if (request.extra_preferences?.trim()) {
    drops.push({
      id: 'extra',
      label: 'Clear extra note',
      request: { ...request, extra_preferences: undefined },
    })
  }
  return drops
}

/** Length in Unicode code points, matching how the backend counts `extra_preferences`. */
export function codePointLength(text: string): number {
  return [...text].length
}

export function truncateCodePoints(text: string, limit: number): string {
  const chars = [...text]
  return chars.length > limit ? chars.slice(0, limit).join('') : text
}
