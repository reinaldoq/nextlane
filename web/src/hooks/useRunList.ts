import { useEffect, useState } from 'react'
import { ApiError, api, isAbortError, type AgentRun, type ListResponse } from '../lib/api'

/** Mission Control is deliberately POLLING, not Realtime -- lower
 * integration risk; GET /api/runs is a read path like every other table
 * (see README's AI rails section / design spec Sec6). */
export const POLL_INTERVAL_MS = 3000

export const DEFAULT_LIMIT = 50

export interface UseRunListResult {
  items: AgentRun[]
  total: number
  loading: boolean
  error: string | null
}

/**
 * Polls `GET /api/runs` every `POLL_INTERVAL_MS`, clearing the interval (and
 * aborting any in-flight request) on unmount -- mirrors
 * `useVehicleList`'s abort-per-request pattern, plus a `setInterval` for the
 * live-refresh behavior Mission Control needs.
 */
export function useRunList(limit: number = DEFAULT_LIMIT): UseRunListResult {
  const [state, setState] = useState<UseRunListResult>({
    items: [],
    total: 0,
    loading: true,
    error: null,
  })

  useEffect(() => {
    let active = true
    let controller: AbortController | undefined

    function poll() {
      controller?.abort()
      controller = new AbortController()
      api
        .get<ListResponse<AgentRun>>('/api/runs', { limit }, controller.signal)
        .then((res) => {
          if (!active) return
          setState({ items: res.items, total: res.total, loading: false, error: null })
        })
        .catch((err: unknown) => {
          if (!active || isAbortError(err)) return
          const message = err instanceof ApiError ? err.message : 'Failed to load agent runs.'
          // Keep the previously loaded runs on screen -- a transient poll
          // failure shouldn't blank out a dashboard that was working a
          // moment ago.
          setState((prev) => ({ ...prev, loading: false, error: message }))
        })
    }

    poll()
    const intervalId = setInterval(poll, POLL_INTERVAL_MS)

    return () => {
      active = false
      clearInterval(intervalId)
      controller?.abort()
    }
  }, [limit])

  return state
}
