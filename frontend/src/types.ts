// Mirrors the API contract in docs/architecture.md section 4 and backend/app/models/.

export type BudgetTier = 'low' | 'medium' | 'high'

export const BUDGET_TIERS: BudgetTier[] = ['low', 'medium', 'high']

export interface TierBounds {
  min?: number
  max?: number
}

export interface Meta {
  locations: string[]
  cuisines: string[]
  budget_tiers: Partial<Record<BudgetTier, TierBounds>>
  rating_range: { min: number; max: number }
}

export interface RecommendationRequest {
  location: string
  budget?: BudgetTier
  cuisines?: string[]
  min_rating?: number
  extra_preferences?: string
  top_k?: number
}

export interface Restaurant {
  id: string
  name: string
  location: string
  area: string
  address: string
  cuisines: string[]
  rating: number
  votes: number
  cost_for_two: number
  budget_tier: BudgetTier
  rest_type: string | null
  listing_types: string[]
  online_order: boolean
  book_table: boolean
  dish_liked: string[]
}

export interface Recommendation {
  rank: number
  restaurant: Restaurant
  explanation: string
}

export interface ResponseMeta {
  candidates_considered: number
  model: string | null
  latency_ms: number
  message: string | null
  cached?: boolean
}

export interface RecommendationResponse {
  source: 'llm' | 'fallback'
  summary: string | null
  relaxations: string[]
  recommendations: Recommendation[]
  meta: ResponseMeta
}

export interface FieldError {
  field: string
  message: string
}

// Request fields that have an input in PreferenceForm, where their 422 messages are shown.
export const FORM_FIELDS = ['location', 'budget', 'cuisines', 'min_rating', 'extra_preferences', 'top_k'] as const

export type FormField = (typeof FORM_FIELDS)[number]

export function isFormField(field: string): field is FormField {
  return (FORM_FIELDS as readonly string[]).includes(field)
}
