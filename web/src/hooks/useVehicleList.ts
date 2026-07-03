import { useEffect, useState } from 'react'
import { ApiError, api, isAbortError, type ListResponse, type Vehicle } from '../lib/api'

/** Columns the API accepts for `sort=field:dir` (kept in sync with the server whitelist). */
export const SORT_FIELDS = ['created_at', 'price_cents', 'year', 'mileage_km'] as const
export type SortField = (typeof SORT_FIELDS)[number]
export type SortDirection = 'asc' | 'desc'

export interface SortState {
  field: SortField
  order: SortDirection
}

export const DEFAULT_SORT: SortState = { field: 'created_at', order: 'desc' }
export const DEFAULT_PAGE_SIZE = 20
export const PAGE_SIZE_OPTIONS = [10, 20, 50, 100]

export interface UseVehicleListParams {
  q: string
  status: Vehicle['status'] | undefined
  sort: SortState
  page: number
  pageSize: number
  /** Bump to re-run the current query without changing any filter. */
  refreshKey: number
}

export interface UseVehicleListResult {
  items: Vehicle[]
  total: number
  loading: boolean
  error: string | null
}

/**
 * Server-driven vehicle list: search, status filter, sort and pagination are
 * all forwarded to `GET /api/vehicles` as query params -- nothing is sorted
 * or filtered client-side.
 */
export function useVehicleList(params: UseVehicleListParams): UseVehicleListResult {
  const { q, status, sort, page, pageSize, refreshKey } = params
  const [state, setState] = useState<UseVehicleListResult>({
    items: [],
    total: 0,
    loading: true,
    error: null,
  })

  useEffect(() => {
    let active = true
    const controller = new AbortController()

    setState((prev) => ({ ...prev, loading: true, error: null }))

    api
      .get<ListResponse<Vehicle>>(
        '/api/vehicles',
        {
          q: q.trim() || undefined,
          status,
          sort: `${sort.field}:${sort.order}`,
          limit: pageSize,
          offset: (page - 1) * pageSize,
        },
        controller.signal,
      )
      .then((res) => {
        if (!active) return
        setState({ items: res.items, total: res.total, loading: false, error: null })
      })
      .catch((err: unknown) => {
        if (!active || isAbortError(err)) return
        const message = err instanceof ApiError ? err.message : 'Failed to load vehicles.'
        // Keep the previously loaded items on screen -- an error banner is enough,
        // the table shouldn't go blank because one request failed.
        setState((prev) => ({ ...prev, loading: false, error: message }))
      })

    return () => {
      active = false
      controller.abort()
    }
  }, [q, status, sort.field, sort.order, page, pageSize, refreshKey])

  return state
}
