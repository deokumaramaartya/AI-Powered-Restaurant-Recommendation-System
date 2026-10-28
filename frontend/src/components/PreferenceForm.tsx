import { useEffect, useId, useMemo, useRef, useState, type CSSProperties, type FormEvent } from 'react'
import {
  BUDGET_LABELS,
  codePointLength,
  formatMinRating,
  formatRating,
  formatTierBounds,
  truncateCodePoints,
} from '../format'
import { BUDGET_TIERS, type BudgetTier, type FieldError, type FormField, type Meta, type RecommendationRequest } from '../types'

const EXTRA_PREFERENCES_LIMIT = 300
const DEFAULT_MIN_RATING = 3.5
const RESULT_COUNT_OPTIONS = [3, 5, 10]
const RATING_STEP = 0.5
const NO_ERRORS: FieldError[] = []

interface Props {
  meta: Meta
  submitting?: boolean
  fieldErrors?: FieldError[]
  seed?: RecommendationRequest | null
  onSubmit: (request: RecommendationRequest) => void
}

function describedBy(...ids: (string | false)[]): string | undefined {
  return ids.filter(Boolean).join(' ') || undefined
}

export function PreferenceForm({
  meta,
  submitting = false,
  fieldErrors = NO_ERRORS,
  seed = null,
  onSubmit,
}: Props) {
  const ids = useId()
  const locationRef = useRef<HTMLSelectElement>(null)
  const [location, setLocation] = useState('')
  const [locationMissing, setLocationMissing] = useState(false)
  const [budget, setBudget] = useState<BudgetTier | ''>('')
  const [cuisines, setCuisines] = useState<string[]>([])
  const [cuisineQuery, setCuisineQuery] = useState('')
  const [minRating, setMinRating] = useState(DEFAULT_MIN_RATING)
  const [extra, setExtra] = useState('')
  const [topK, setTopK] = useState(5)

  useEffect(() => {
    if (!seed) return
    setLocation(seed.location)
    setBudget(seed.budget ?? '')
    setCuisines(seed.cuisines ?? [])
    setMinRating(seed.min_rating ?? DEFAULT_MIN_RATING)
    setExtra(seed.extra_preferences ?? '')
    setTopK(seed.top_k ?? 5)
    setLocationMissing(false)
  }, [seed])

  const errorText = (field: FormField) =>
    fieldErrors
      .filter((error) => error.field === field)
      .map((error) => error.message)
      .join(' ')
  const errorId = (field: FormField) => errorText(field) !== '' && `${ids}-${field}-error`
  const errorMessage = (field: FormField) => {
    const text = errorText(field)
    return text ? (
      <p id={`${ids}-${field}-error`} className="field__error">
        {text}
      </p>
    ) : null
  }

  const tiers = BUDGET_TIERS.filter((tier) => meta.budget_tiers[tier])
  const visibleCuisines = useMemo(() => {
    const query = cuisineQuery.trim().toLocaleLowerCase()
    return query ? meta.cuisines.filter((name) => name.toLocaleLowerCase().includes(query)) : meta.cuisines
  }, [meta.cuisines, cuisineQuery])
  const { min: ratingMin, max: ratingMax } = meta.rating_range

  const toggleCuisine = (name: string) => {
    setCuisines((current) => (current.includes(name) ? current.filter((item) => item !== name) : [...current, name]))
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (submitting) return
    if (!location) {
      setLocationMissing(true)
      locationRef.current?.focus()
      return
    }
    const request: RecommendationRequest = { location, min_rating: minRating, top_k: topK }
    if (budget) request.budget = budget
    if (cuisines.length > 0) request.cuisines = cuisines
    if (extra.trim()) request.extra_preferences = extra.trim()
    onSubmit(request)
  }

  const extraLength = codePointLength(extra)
  const locationInvalid = locationMissing || errorText('location') !== ''

  return (
    <form className="form" onSubmit={handleSubmit} aria-label="Restaurant preferences" noValidate>
      <div className="field">
        <label className="field__label field__label--required" htmlFor={`${ids}-location`}>
          Locality
        </label>
        <div className="select">
          <select
            ref={locationRef}
            id={`${ids}-location`}
            className="input"
            value={location}
            onChange={(event) => {
              setLocation(event.target.value)
              setLocationMissing(false)
            }}
            aria-describedby={describedBy(
              `${ids}-location-note`,
              locationMissing && `${ids}-location-missing`,
              errorId('location'),
            )}
            aria-invalid={locationInvalid || undefined}
            required
          >
            <option value="">Choose a neighbourhood</option>
            {meta.locations.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </div>
        <p id={`${ids}-location-note`} className="field__hint">
          Covers Bangalore localities only.
        </p>
        {locationMissing ? (
          <p id={`${ids}-location-missing`} className="field__error" role="alert">
            Pick a locality to start — everything else is optional.
          </p>
        ) : null}
        {errorMessage('location')}
      </div>

      <fieldset className="field" aria-describedby={describedBy(errorId('budget'))}>
        <legend className="field__label">Budget for two</legend>
        <div className="segmented segmented--budget" style={{ '--cells': tiers.length + 1 } as CSSProperties}>
          <label className="segmented__option">
            <input
              type="radio"
              name={`${ids}-budget`}
              value=""
              checked={budget === ''}
              onChange={() => setBudget('')}
            />
            <span>
              Any
              <small>all</small>
            </span>
          </label>
          {tiers.map((tier) => (
            <label key={tier} className="segmented__option">
              <input
                type="radio"
                name={`${ids}-budget`}
                value={tier}
                checked={budget === tier}
                onChange={() => setBudget(tier)}
              />
              <span>
                {BUDGET_LABELS[tier]}
                <small>{formatTierBounds(meta.budget_tiers[tier])}</small>
              </span>
            </label>
          ))}
        </div>
        {errorMessage('budget')}
      </fieldset>

      <fieldset className="field" aria-describedby={describedBy(errorId('cuisines'))}>
        <legend className="field__label field__label--split">
          Cuisines <span className="field__aside">any match</span>
        </legend>
        {cuisines.length > 0 ? (
          <ul className="chips" aria-label="Selected cuisines">
            {cuisines.map((name) => (
              <li key={name}>
                <button type="button" className="chip" onClick={() => toggleCuisine(name)} aria-label={`Remove ${name}`}>
                  {name}
                  <span aria-hidden="true">×</span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        <label className="visually-hidden" htmlFor={`${ids}-cuisine-search`}>
          Search cuisines
        </label>
        <input
          id={`${ids}-cuisine-search`}
          type="search"
          className="input input--search"
          placeholder="Search cuisines"
          value={cuisineQuery}
          onChange={(event) => setCuisineQuery(event.target.value)}
          autoComplete="off"
        />
        <div className="cuisine-list" role="group" aria-label="Cuisine options">
          {visibleCuisines.length === 0 ? (
            <p className="cuisine-list__empty">No cuisine called that.</p>
          ) : (
            visibleCuisines.map((name) => (
              <label key={name} className="cuisine-list__option">
                <input type="checkbox" checked={cuisines.includes(name)} onChange={() => toggleCuisine(name)} />
                <span>{name}</span>
              </label>
            ))
          )}
        </div>
        {errorMessage('cuisines')}
      </fieldset>

      <div className="field">
        <div className="field__row">
          <label className="field__label" htmlFor={`${ids}-rating`}>
            Minimum rating
          </label>
          <output className="rating-value" htmlFor={`${ids}-rating`} data-testid="rating-value">
            {formatMinRating(minRating)}
          </output>
        </div>
        <input
          id={`${ids}-rating`}
          type="range"
          className="range"
          min={ratingMin}
          max={ratingMax}
          step={RATING_STEP}
          value={minRating}
          onChange={(event) => setMinRating(Number(event.target.value))}
          aria-valuetext={minRating > 0 ? `${formatRating(minRating)} stars or higher` : 'Any rating'}
          aria-describedby={describedBy(errorId('min_rating'))}
          aria-invalid={errorText('min_rating') !== '' || undefined}
        />
        <div className="range-scale" aria-hidden="true">
          <span>{ratingMin}</span>
          <span>{(ratingMin + ratingMax) / 2}</span>
          <span>{ratingMax}</span>
        </div>
        {errorMessage('min_rating')}
      </div>

      <div className="field">
        <label className="field__label" htmlFor={`${ids}-extra`}>
          Anything else?
        </label>
        <textarea
          id={`${ids}-extra`}
          className="input input--textarea"
          rows={3}
          placeholder="Quiet enough to talk, rooftop, veg-friendly…"
          value={extra}
          onChange={(event) => setExtra(truncateCodePoints(event.target.value, EXTRA_PREFERENCES_LIMIT))}
          aria-describedby={describedBy(`${ids}-extra-count`, errorId('extra_preferences'))}
          aria-invalid={errorText('extra_preferences') !== '' || undefined}
        />
        <span
          id={`${ids}-extra-count`}
          className={extraLength >= EXTRA_PREFERENCES_LIMIT ? 'counter counter--full' : 'counter'}
          data-testid="extra-count"
        >
          {extraLength}/{EXTRA_PREFERENCES_LIMIT}
        </span>
        {errorMessage('extra_preferences')}
      </div>

      <fieldset className="field field--inline" aria-describedby={describedBy(errorId('top_k'))}>
        <legend className="field__label">Results</legend>
        <div className="segmented segmented--count">
          {RESULT_COUNT_OPTIONS.map((count) => (
            <label key={count} className="segmented__option">
              <input
                type="radio"
                name={`${ids}-top-k`}
                value={count}
                checked={topK === count}
                onChange={() => setTopK(count)}
              />
              <span>{count}</span>
            </label>
          ))}
        </div>
        {errorMessage('top_k')}
      </fieldset>

      <div className="form__submit">
        <button type="submit" className="cta" disabled={submitting}>
          {submitting ? 'Finding restaurants…' : 'Find restaurants'}
        </button>
      </div>
    </form>
  )
}
