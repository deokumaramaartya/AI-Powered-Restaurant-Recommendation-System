import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, getMeta, getRecommendations, isAbortError } from './api/client'
import { ErrorNotice } from './components/ErrorNotice'
import { LoadingState } from './components/LoadingState'
import { LocalityUnavailable } from './components/LocalityUnavailable'
import { PreferenceForm } from './components/PreferenceForm'
import { IdleState, NoDataState } from './components/ResultsPlaceholder'
import { ResultsList } from './components/ResultsList'
import { describeFilters } from './format'
import type { FieldError, Meta, RecommendationRequest, RecommendationResponse } from './types'
import './App.css'

// Must match the single-column breakpoint in App.css.
const STACKED_LAYOUT_QUERY = '(max-width: 820px)'
const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)'

type MetaState = { status: 'loading' } | { status: 'ready'; meta: Meta } | { status: 'error'; error: unknown }

type ResultsState =
  | { status: 'idle' }
  | { status: 'loading'; request: RecommendationRequest }
  | { status: 'done'; request: RecommendationRequest; response: RecommendationResponse }
  | { status: 'error'; request: RecommendationRequest; error: unknown }

const NO_FIELD_ERRORS: FieldError[] = []

function fieldErrorsOf(state: ResultsState): FieldError[] {
  if (state.status === 'error' && state.error instanceof ApiError && state.error.kind === 'validation') {
    return state.error.fieldErrors
  }
  return NO_FIELD_ERRORS
}

function announce(state: ResultsState): string {
  if (state.status === 'loading') return 'Finding restaurants…'
  if (state.status === 'error') return 'The search did not complete.'
  if (state.status !== 'done') return ''
  const count = state.response.recommendations.length
  if (count === 0) return 'No restaurants matched.'
  const found = `${count === 1 ? '1 restaurant' : `${count} restaurants`} found in ${state.request.location}`
  return state.response.source === 'fallback' ? `${found}, ranked without AI` : found
}

function matches(query: string): boolean {
  return window.matchMedia?.(query).matches ?? false
}

function subscribeMedia(query: string, onChange: (matchesQuery: boolean) => void): () => void {
  const media = window.matchMedia?.(query)
  if (!media) return () => {}
  const listener = () => onChange(media.matches)
  media.addEventListener('change', listener)
  return () => media.removeEventListener('change', listener)
}

function scrollToTop(element: HTMLElement | null) {
  element?.scrollIntoView?.({ behavior: matches(REDUCED_MOTION_QUERY) ? 'auto' : 'smooth', block: 'start' })
}

function App() {
  const [metaState, setMetaState] = useState<MetaState>({ status: 'loading' })
  const [results, setResults] = useState<ResultsState>({ status: 'idle' })
  const [stacked, setStacked] = useState(() => matches(STACKED_LAYOUT_QUERY))
  const [formOpen, setFormOpen] = useState(true)
  const [formSeed, setFormSeed] = useState<RecommendationRequest | null>(null)
  const metaController = useRef<AbortController | null>(null)
  const searchController = useRef<AbortController | null>(null)
  const latestSearch = useRef(0)
  const panelRef = useRef<HTMLElement>(null)
  const outputRef = useRef<HTMLElement>(null)
  const pendingResultsScroll = useRef(false)
  const pendingFormFocus = useRef(false)

  useEffect(() => subscribeMedia(STACKED_LAYOUT_QUERY, setStacked), [])

  const fetchMeta = useCallback(() => {
    metaController.current?.abort()
    const controller = new AbortController()
    metaController.current = controller
    getMeta({ signal: controller.signal }).then(
      (meta) => setMetaState({ status: 'ready', meta }),
      (error: unknown) => {
        if (!isAbortError(error)) setMetaState({ status: 'error', error })
      },
    )
    return controller
  }, [])

  useEffect(() => {
    const controller = fetchMeta()
    return () => controller.abort()
  }, [fetchMeta])

  const retryMeta = () => {
    setMetaState({ status: 'loading' })
    fetchMeta()
  }

  const search = useCallback(async (request: RecommendationRequest) => {
    searchController.current?.abort()
    const controller = new AbortController()
    searchController.current = controller
    const searchId = ++latestSearch.current
    setFormOpen(false)
    setResults({ status: 'loading', request })
    if (matches(STACKED_LAYOUT_QUERY)) pendingResultsScroll.current = true
    try {
      const response = await getRecommendations(request, { signal: controller.signal })
      if (searchId === latestSearch.current) setResults({ status: 'done', request, response })
    } catch (error) {
      if (isAbortError(error) || searchId !== latestSearch.current) return
      setResults({ status: 'error', request, error })
    }
  }, [])

  useEffect(() => () => searchController.current?.abort(), [])

  useEffect(() => {
    if (!pendingResultsScroll.current) return
    pendingResultsScroll.current = false
    scrollToTop(outputRef.current)
  }, [results.status, formOpen])

  useEffect(() => {
    if (!pendingFormFocus.current || !formOpen) return
    pendingFormFocus.current = false
    scrollToTop(panelRef.current)
    panelRef.current?.querySelector('select')?.focus({ preventScroll: true })
  }, [formOpen])

  const editPreferences = () => {
    if (stacked && !formOpen) {
      pendingFormFocus.current = true
      setFormOpen(true)
      return
    }
    scrollToTop(panelRef.current)
    panelRef.current?.querySelector('select')?.focus({ preventScroll: true })
  }

  const meta = metaState.status === 'ready' ? metaState.meta : null
  const metaIsEmpty = meta !== null && meta.locations.length === 0
  const submitted = results.status === 'idle' ? null : results.request
  const hideChrome = stacked && submitted !== null && !formOpen
  const layoutMode = !stacked
    ? 'split'
    : submitted !== null
      ? formOpen
        ? 'editing'
        : 'results'
      : metaIsEmpty
        ? 'editing'
        : 'compose'

  return (
    <div className={`app app--${layoutMode}`}>
      <header className="masthead" hidden={hideChrome}>
        <p className="masthead__eyebrow">Bangalore · Tonight</p>
        <h1 className="masthead__title">
          Where should we <span className="masthead__accent">eat?</span>
        </h1>
        <p className="masthead__lede">
          Pick a neighbourhood and a mood. We’ll shortlist the places worth the auto ride — and tell you why.
        </p>
      </header>

      <main className="layout">
        <section className="panel" aria-label="Your preferences" ref={panelRef} hidden={hideChrome}>
          {metaState.status === 'loading' ? <p className="panel__status">Loading localities and cuisines…</p> : null}
          {metaState.status === 'error' ? <LocalityUnavailable error={metaState.error} onRetry={retryMeta} /> : null}
          {metaIsEmpty ? <LocalityUnavailable error={null} /> : null}
          {meta && !metaIsEmpty ? (
            <PreferenceForm
              meta={meta}
              submitting={results.status === 'loading'}
              fieldErrors={fieldErrorsOf(results)}
              seed={formSeed}
              onSubmit={search}
            />
          ) : null}
        </section>

        <section className="output" aria-label="Recommendations" ref={outputRef}>
          <p className="visually-hidden" aria-live="polite">
            {announce(results)}
          </p>
          {submitted && hideChrome ? (
            <div className="condensed">
              <div className="condensed__text">
                <p className="condensed__place">{submitted.location}</p>
                <p className="condensed__filters">{describeFilters(submitted).join(' · ')}</p>
              </div>
              <button type="button" className="button button--outline button--compact" onClick={editPreferences} aria-label="Edit preferences">
                Edit
              </button>
            </div>
          ) : null}
          {results.status === 'idle' ? metaIsEmpty ? <NoDataState /> : <IdleState /> : null}
          {results.status === 'loading' ? (
            <LoadingState location={results.request.location} count={results.request.top_k} />
          ) : null}
          {results.status === 'error' ? (
            <ErrorNotice error={results.error} onRetry={() => void search(results.request)} />
          ) : null}
          {results.status === 'done' ? (
            <ResultsList
              response={results.response}
              request={results.request}
              onDropFilter={(next) => {
                setFormSeed(next)
                void search(next)
              }}
              onEditPreferences={editPreferences}
            />
          ) : null}
        </section>
      </main>
    </div>
  )
}

export default App
