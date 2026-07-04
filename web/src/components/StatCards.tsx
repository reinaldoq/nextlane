import { useEffect, useState } from 'react'
import { Alert, Card, Col, Flex, Row, Statistic, Typography, theme } from 'antd'
import { ApiError, api, isAbortError, type Vehicle, type VehicleStats } from '../lib/api'

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

interface StatCardsProps {
  /** Bump to re-run the stats request (kept in step with the table's own reloads). */
  refreshKey: number
}

/** Three status counts, read from a single GET /api/vehicles/stats request. */
function StatCards({ refreshKey }: StatCardsProps) {
  const { token } = theme.useToken()
  const [stats, setStats] = useState<VehicleStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    const controller = new AbortController()

    setLoading(true)
    setError(null)

    // One request for every card: the counts come pre-aggregated by the API.
    void api
      .get<VehicleStats>('/api/vehicles/stats', undefined, controller.signal)
      .then((result) => {
        if (!active) return
        setStats(result)
        setLoading(false)
      })
      .catch((reason: unknown) => {
        if (!active || isAbortError(reason)) return
        setError(reason instanceof ApiError ? reason.message : 'Failed to load stats.')
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
                value={stats?.[status] ?? undefined}
                loading={loading && stats === null}
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
