import type { FieldError, Meta, RecommendationRequest, RecommendationResponse } from '../types'

export function resolveApiBase(raw: string | undefined): string {
  return (raw ?? '').trim().replace(/\/+$/, '')
}

const API_BASE = resolveApiBase(import.meta.env.VITE_API_BASE_URL)

export const REQUEST_TIMEOUT_MS = 20_000

export type ApiErrorKind = 'network' | 'timeout' | 'unavailable' | 'validation' | 'server'

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status?: number
  readonly fieldErrors: FieldError[]

  constructor(kind: ApiErrorKind, message: string, options: { status?: number; fieldErrors?: FieldError[] } = {}) {
    super(message)
    this.name = 'ApiError'
    this.kind = kind
    this.status = options.status
    this.fieldErrors = options.fieldErrors ?? []
  }
}

export interface RequestOptions {
  signal?: AbortSignal
  timeoutMs?: number
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    return null
  }
}

function detailText(body: unknown): string | null {
  if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') {
    return body.detail
  }
  return null
}

// 422 bodies look like {"detail": [{"loc": ["body", "location"], "field": "location", "msg": "..."}]}.
function parseFieldErrors(body: unknown): FieldError[] {
  if (!body || typeof body !== 'object' || !('detail' in body) || !Array.isArray(body.detail)) {
    return []
  }
  return body.detail.flatMap((item: unknown) => {
    if (!item || typeof item !== 'object' || !('msg' in item) || typeof item.msg !== 'string') {
      return []
    }
    if ('field' in item && typeof item.field === 'string') {
      return [{ field: item.field, message: item.msg }]
    }
    const loc = 'loc' in item && Array.isArray(item.loc) ? item.loc : []
    const path = loc[0] === 'body' ? loc.slice(1) : loc
    return [{ field: path.length > 0 ? String(path[0]) : '', message: item.msg }]
  })
}

async function toApiError(response: Response): Promise<ApiError> {
  const body = await readJson(response)
  const { status } = response
  // The Vite dev proxy answers 502 when the backend is not running.
  if (status === 502 || status === 504) {
    return new ApiError('network', "Can't reach the server.", { status })
  }
  if (status === 503) {
    return new ApiError('unavailable', detailText(body) ?? 'Data not loaded. Run ingestion and restart the server.', {
      status,
    })
  }
  if (status === 422) {
    const fieldErrors = parseFieldErrors(body)
    const message = fieldErrors.map((error) => error.message).join(' ') || 'Some preferences were not accepted.'
    return new ApiError('validation', message, { status, fieldErrors })
  }
  return new ApiError('server', detailText(body) ?? `Server error (${status}).`, { status })
}

async function request<T>(path: string, init: RequestInit, options: RequestOptions = {}): Promise<T> {
  const { signal, timeoutMs = REQUEST_TIMEOUT_MS } = options
  if (signal?.aborted) {
    throw new DOMException('Request cancelled', 'AbortError')
  }
  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
  const cancel = () => controller.abort()
  signal?.addEventListener('abort', cancel)

  try {
    const response = await fetch(`${API_BASE}${path}`, { ...init, signal: controller.signal })
    if (!response.ok) {
      throw await toApiError(response)
    }
    return (await response.json()) as T
  } catch (error) {
    if (error instanceof ApiError) {
      throw error
    }
    if (timedOut) {
      throw new ApiError('timeout', 'The server took too long to respond.')
    }
    if (signal?.aborted) {
      throw new DOMException('Request cancelled', 'AbortError')
    }
    if (error instanceof SyntaxError) {
      throw new ApiError('server', 'The server sent a response that could not be read.')
    }
    throw new ApiError('network', "Can't reach the server.")
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', cancel)
  }
}

export function getMeta(options?: RequestOptions): Promise<Meta> {
  return request<Meta>('/api/meta', { method: 'GET' }, options)
}

export function getRecommendations(
  body: RecommendationRequest,
  options?: RequestOptions,
): Promise<RecommendationResponse> {
  return request<RecommendationResponse>(
    '/api/recommendations',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
    options,
  )
}
