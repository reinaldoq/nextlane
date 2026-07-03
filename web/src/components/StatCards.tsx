import { useEffect, useState } from 'react'
import { Alert, Card, Col, Flex, Row, Statistic, Typography, theme } from 'antd'
import { ApiError, api, isAbortError, type ListResponse, type Vehicle } from '../lib/api'

const { Text } = Typography

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

const STATUSES: Status[] = ['available', 'reserved', 'sold']

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

    // TODO: consolidate these 3 count requests (+ the table's own list request:
    // 4 requests per refresh) into a single future GET /api/vehicles/stats.
    void Promise.allSettled(
      STATUSES.map((status) =>
        api.get<ListResponse<Vehicle>>('/api/vehicles', { status, limit: 1 }, controller.signal),
      ),
    ).then((results) => {
      if (!active) return

      const nextCounts: Partial<Record<Status, number>> = {}
      let failure: unknown = null
      results.forEach((result, index) => {
        const status = STATUSES[index]
        if (status === undefined) return
        if (result.status === 'fulfilled') {
          nextCounts[status] = result.value.total
        } else if (!isAbortError(result.reason)) {
          failure = result.reason
        }
      })

      // Render whichever counts succeeded; a single shared line covers failures.
      setCounts((prev) => ({ ...prev, ...nextCounts }))
      if (failure !== null) {
        setError(failure instanceof ApiError ? failure.message : 'Failed to load stats.')
      }
      setLoading(false)
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
    <Flex vertical gap={8}>
      <Text type="secondary" style={{ fontSize: 12 }}>
        Total inventory — counts are not affected by the search or filters below.
      </Text>
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
          style={{ marginTop: 4 }}
          banner
        />
      )}
    </Flex>
  )
}

export default StatCards
