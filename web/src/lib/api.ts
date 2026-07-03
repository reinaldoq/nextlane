import { supabase } from './supabase'

/** Error thrown by {@link apiFetch} for any non-2xx response. */
export class ApiError extends Error {
  code: string
  status: number
  details?: Record<string, unknown>

  constructor(
    status: number,
    code: string,
    message: string,
    details?: Record<string, unknown>,
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

export type QueryParams = Record<string, string | number | boolean | undefined>

export interface ApiFetchOptions {
  method?: string
  /** Plain value; JSON.stringify-ed internally. */
  body?: unknown
  /** Query string parameters; `undefined` values are skipped. */
  params?: QueryParams
  /** Forwarded to `fetch`; lets callers cancel in-flight requests (e.g. debounced search). */
  signal?: AbortSignal
}

function buildQuery(params?: QueryParams): string {
  if (!params) return ''
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value))
  }
  const qs = search.toString()
  return qs ? `?${qs}` : ''
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/**
 * Typed fetch wrapper for the same-origin `/api` backend.
 *
 * Attaches the current Supabase session's access token as a Bearer
 * authorization header (when present) and a JSON content type (when a body
 * is given). Non-2xx responses are parsed against the API's top-level error
 * envelope `{code, message, details}` and thrown as an {@link ApiError};
 * malformed/non-JSON bodies fall back to a generic code.
 */
export async function apiFetch<T>(path: string, opts: ApiFetchOptions = {}): Promise<T> {
  const { data } = await supabase.auth.getSession()
  const token = data.session?.access_token

  const headers = new Headers()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const init: RequestInit = { method: opts.method ?? 'GET', headers, signal: opts.signal }
  if (opts.body !== undefined) {
    headers.set('Content-Type', 'application/json')
    init.body = JSON.stringify(opts.body)
  }

  const res = await fetch(`${path}${buildQuery(opts.params)}`, init)

  if (!res.ok) {
    let code = 'error'
    let message = res.statusText || `HTTP ${res.status}`
    let details: Record<string, unknown> | undefined

    try {
      const body: unknown = await res.json()
      if (isRecord(body) && typeof body.code === 'string') {
        code = body.code
        if (typeof body.message === 'string') message = body.message
        if (isRecord(body.details)) details = body.details
      }
    } catch {
      // Response body wasn't JSON (or was empty) — keep the fallback code/message.
    }

    throw new ApiError(res.status, code, message, details)
  }

  if (res.status === 204) {
    return undefined as T
  }

  return (await res.json()) as T
}

/** Thin verb helpers over {@link apiFetch}. */
export const api = {
  get: <T>(path: string, params?: QueryParams, signal?: AbortSignal): Promise<T> =>
    apiFetch<T>(path, { params, signal }),
  post: <T>(path: string, body?: unknown): Promise<T> =>
    apiFetch<T>(path, { method: 'POST', body }),
  patch: <T>(path: string, body: unknown): Promise<T> =>
    apiFetch<T>(path, { method: 'PATCH', body }),
  del: (path: string): Promise<undefined> => apiFetch<undefined>(path, { method: 'DELETE' }),
}

export interface Vehicle {
  id: string
  vin: string
  make: string
  model: string
  year: number
  price_cents: number
  mileage_km: number
  status: 'available' | 'reserved' | 'sold'
  created_at: string
  updated_at: string
}

export interface ListResponse<T> {
  items: T[]
  total: number
}
