import { useEffect, useState } from 'react'
import { Alert, Card, Col, Row, Statistic, theme } from 'antd'
import { ApiError, api, type ListResponse, type Vehicle } from '../lib/api'

type Status = Vehicle['status']

interface StatDef {
  status: Status
  label: string
}

const STAT_DEFS: StatDef[] = [
  { status: 'available', label: 'Available' },
  { status: 'reserved', label: 'Reserved' },
  { status: 'sold', label: 'Sold' },
]

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError'
}

interface StatCardsProps {
  /** Bump to re-run the three count requests (kept in step with the table's own reloads). */
  refreshKey: number
}

/** Three status counts, each a `total` read from a `limit: 1` list request. */
function StatCards({ refreshKey }: StatCardsProps) {
  const { token } = theme.useToken()
  const [counts, setCounts] = useState<Record<Status, number | null>>({
    available: null,
    reserved: null,
    sold: null,
  })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    const controller = new AbortController()

    setLoading(true)
    setError(null)

    Promise.all([
      api.get<ListResponse<Vehicle>>(
        '/api/vehicles',
        { status: 'available', limit: 1 },
        controller.signal,
      ),
      api.get<ListResponse<Vehicle>>(
        '/api/vehicles',
        { status: 'reserved', limit: 1 },
        controller.signal,
      ),
      api.get<ListResponse<Vehicle>>(
        '/api/vehicles',
        { status: 'sold', limit: 1 },
        controller.signal,
      ),
    ])
      .then(([available, reserved, sold]) => {
        if (!active) return
        setCounts({ available: available.total, reserved: reserved.total, sold: sold.total })
        setError(null)
      })
      .catch((err: unknown) => {
        if (!active || isAbortError(err)) return
        setError(err instanceof ApiError ? err.message : 'Failed to load stats.')
      })
      .finally(() => {
        if (active) setLoading(false)
      })

    return () => {
      active = false
      controller.abort()
    }
  }, [refreshKey])

  const accentColor: Record<Status, string> = {
    available: token.colorSuccess,
    reserved: token.colorWarning,
    sold: token.colorTextTertiary,
  }

  return (
    <div>
      <Row gutter={[16, 16]}>
        {STAT_DEFS.map(({ status, label }) => (
          <Col key={status} xs={24} sm={8}>
            <Card
              variant="borderless"
              style={{
                borderTop: `3px solid ${accentColor[status]}`,
                boxShadow: token.boxShadowTertiary,
              }}
              styles={{ body: { padding: '18px 20px' } }}
            >
              <Statistic
                title={
                  <span
                    style={{
                      textTransform: 'uppercase',
                      letterSpacing: 0.6,
                      fontSize: 12,
                      fontWeight: 600,
                      color: token.colorTextSecondary,
                    }}
                  >
                    {label}
                  </span>
                }
                value={counts[status] ?? undefined}
                loading={loading && counts[status] === null}
                valueStyle={{
                  color: accentColor[status],
                  fontVariantNumeric: 'tabular-nums',
                  fontWeight: 600,
                }}
              />
            </Card>
          </Col>
        ))}
      </Row>
      {error !== null && (
        <Alert
          type="warning"
          showIcon
          message={error}
          style={{ marginTop: 12 }}
          banner
        />
      )}
    </div>
  )
}

export default StatCards
