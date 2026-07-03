import { supabase } from './supabase'

/** Error thrown by {@link apiFetch} for any non-2xx response. */
export class ApiError extends Error {
  code: string
  status: number
  details?: unknown

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

interface ErrorEnvelope {
  code?: unknown
  message?: unknown
  details?: unknown
}

function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  return typeof value === 'object' && value !== null
}

/**
 * Typed fetch wrapper for the same-origin `/api` backend.
 *
 * Attaches the current Supabase session's access token as a Bearer
 * authorization header (when present) and a JSON content type (when the
 * request has a body). Non-2xx responses are parsed against the API's
 * top-level error envelope `{code, message, details}` and thrown as an
 * {@link ApiError}; malformed/non-JSON bodies fall back to a generic code.
 */
export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { data } = await supabase.auth.getSession()
  const token = data.session?.access_token

  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body !== undefined) headers.set('Content-Type', 'application/json')

  const res = await fetch(path, { ...init, headers })

  if (!res.ok) {
    let code = 'error'
    let message = res.statusText
    let details: unknown

    try {
      const body: unknown = await res.json()
      if (isErrorEnvelope(body) && typeof body.code === 'string') {
        code = body.code
        if (typeof body.message === 'string') message = body.message
        details = body.details
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
