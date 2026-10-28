import { describe, expect, it } from 'vitest'
import { describeFilters, filterDrops } from './format'

describe('describeFilters', () => {
  it('includes the anything-else note so extras are not silently dropped from the summary', () => {
    expect(
      describeFilters({
        location: 'Jayanagar',
        budget: 'medium',
        min_rating: 4,
        extra_preferences: 'quiet enough to talk, table booking',
      }),
    ).toEqual(['Medium budget', 'Any cuisine', '4.0+', 'quiet enough to talk, table booking'])
  })
})

describe('filterDrops', () => {
  it('offers one action per user-set filter and never location', () => {
    const request = {
      location: 'Indiranagar',
      budget: 'high' as const,
      cuisines: ['Bengali'],
      min_rating: 3.5,
      extra_preferences: 'cozy dining',
      top_k: 5,
    }
    const drops = filterDrops(request)
    expect(drops.map((item) => item.id)).toEqual(['cuisines', 'budget', 'rating', 'extra'])
    expect(drops.find((item) => item.id === 'cuisines')?.request.cuisines).toEqual([])
    expect(drops.every((item) => item.request.location === 'Indiranagar')).toBe(true)
  })
})
