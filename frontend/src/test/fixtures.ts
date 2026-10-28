import type { Meta, Recommendation, RecommendationResponse, Restaurant } from '../types'

export const sampleMeta: Meta = {
  locations: ['BTM', 'HSR', 'Indiranagar', 'Koramangala 5th Block'],
  cuisines: ['Chinese', 'Continental', 'Italian', 'Mexican', 'North Indian', 'South Indian'],
  budget_tiers: {
    low: { max: 300 },
    medium: { min: 301, max: 600 },
    high: { min: 601 },
  },
  rating_range: { min: 0, max: 5 },
}

export function makeRestaurant(overrides: Partial<Restaurant> = {}): Restaurant {
  return {
    id: 'r-trattoria',
    name: 'Example Trattoria',
    location: 'Indiranagar',
    area: 'Indiranagar',
    address: '100 Feet Road, Indiranagar, Bangalore',
    cuisines: ['Italian', 'Continental'],
    rating: 4.4,
    votes: 2100,
    cost_for_two: 1200,
    budget_tier: 'high',
    rest_type: 'Casual Dining',
    listing_types: ['Dine-out'],
    online_order: true,
    book_table: true,
    dish_liked: ['Pasta'],
    ...overrides,
  }
}

export function makeRecommendation(rank: number, overrides: Partial<Restaurant> = {}, explanation?: string): Recommendation {
  return {
    rank,
    restaurant: makeRestaurant({ id: `r-${rank}`, name: `Restaurant ${rank}`, ...overrides }),
    explanation: explanation ?? `Rated 4.4 and about ₹1,200 for two, pick number ${rank}.`,
  }
}

export function makeResponse(overrides: Partial<RecommendationResponse> = {}): RecommendationResponse {
  return {
    source: 'llm',
    summary: 'Strong Italian picks with table booking.',
    relaxations: [],
    recommendations: [1, 2, 3, 4, 5].map((rank) => makeRecommendation(rank)),
    meta: { candidates_considered: 18, model: 'openai/gpt-oss-120b', latency_ms: 1840, message: null },
    ...overrides,
  }
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}
