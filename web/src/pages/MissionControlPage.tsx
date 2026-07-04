import { useMemo, useState } from 'react'
import type { CSSProperties } from 'react'
import { Alert, Badge, Card, Empty, Flex, Table, Tag, Typography, theme } from 'antd'
import type { TableColumnsType } from 'antd'
import RunDetailDrawer from '../components/RunDetailDrawer'
import { POLL_INTERVAL_MS, useRunList } from '../hooks/useRunList'
import type { AgentRun } from '../lib/api'

const { Title, Text } = Typography

const ENGINE_LABEL: Record<string, string> = {
  claude: 'Claude',
  codex: 'Codex',
  gemini: 'Gemini',
}

const costFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
})
const startedFormatter = new Intl.DateTimeFormat('en-GB', {
  dateStyle: 'medium',
  timeStyle: 'short',
})

/** Live in-app dashboard of rails agent runs -- POLLING (not Realtime), the
 * deliberate lower-integration-risk choice: GET /api/runs is a read path
 * like every other table (see README's AI rails section). */
function MissionControlPage() {
  const { token } = theme.useToken()
  const { items, total, loading, error } = useRunList()
  const [selectedRun, setSelectedRun] = useState<AgentRun | null>(null)

  const chipStyle = useMemo(() => {
    const styles: Record<'info' | 'success' | 'warning' | 'error' | 'default', CSSProperties> = {
      info: { color: token.colorInfo, background: token.colorInfoBg, borderColor: token.colorInfoBorder },
      success: {
        color: token.colorSuccess,
        background: token.colorSuccessBg,
        borderColor: token.colorSuccessBorder,
      },
      warning: {
        color: token.colorWarning,
        background: token.colorWarningBg,
        borderColor: token.colorWarningBorder,
      },
      error: {
        color: token.colorError,
        background: token.colorErrorBg,
        borderColor: token.colorErrorBorder,
      },
      default: {
        color: token.colorTextSecondary,
        background: token.colorFillTertiary,
        borderColor: token.colorBorderSecondary,
      },
    }
    return styles
  }, [token])

  const engineTagStyle: CSSProperties = {
    color: token.colorPrimary,
    background: token.colorPrimaryBg,
    borderColor: token.colorPrimaryBorder,
  }

  function statusChip(run: AgentRun) {
    if (run.status === 'running') {
      return <Tag style={chipStyle.info}>Running</Tag>
    }
    if (run.status === 'pr_opened') {
      if (run.review_verdict === 'APPROVE') return <Tag style={chipStyle.success}>Approved</Tag>
      if (run.review_verdict === 'REQUEST_CHANGES') {
        return <Tag style={chipStyle.warning}>Changes requested</Tag>
      }
      return <Tag style={chipStyle.success}>PR opened</Tag>
    }
    if (run.status === 'no_changes' || run.status === 'completed_no_pr') {
      return (
        <Tag style={chipStyle.default}>
          {run.status === 'no_changes' ? 'No changes' : 'Completed (no PR)'}
        </Tag>
      )
    }
    // gate_failed | timeout | error
    return <Tag style={chipStyle.error}>{run.status.replace(/_/g, ' ')}</Tag>
  }

  const columns: TableColumnsType<AgentRun> = [
    {
      title: 'Engine',
      key: 'engine',
      render: (_value: unknown, run: AgentRun) => (
        <Flex vertical gap={2}>
          <Tag style={engineTagStyle}>{ENGINE_LABEL[run.engine] ?? run.engine}</Tag>
          {run.reviewer_engine !== null && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              → {ENGINE_LABEL[run.reviewer_engine] ?? run.reviewer_engine}
            </Text>
          )}
        </Flex>
      ),
    },
    {
      title: 'Task',
      key: 'task',
      render: (_value: unknown, run: AgentRun) => (
        <Flex vertical gap={2} style={{ maxWidth: 360 }}>
          <Text type="secondary" style={{ fontSize: 12, textTransform: 'uppercase' }}>
            {run.task_kind}
          </Text>
          <Text ellipsis={{ tooltip: run.task_summary }}>{run.task_summary}</Text>
        </Flex>
      ),
    },
    {
      title: 'Status',
      key: 'status',
      render: (_value: unknown, run: AgentRun) => statusChip(run),
    },
    {
      title: 'Retries',
      dataIndex: 'retries',
      key: 'retries',
      align: 'right',
    },
    {
      title: 'Cost',
      dataIndex: 'cost_usd',
      key: 'cost_usd',
      align: 'right',
      render: (cost: number | null) => (cost !== null ? costFormatter.format(cost) : '—'),
    },
    {
      title: 'Started',
      dataIndex: 'ts_iso',
      key: 'ts_iso',
      render: (value: string) => startedFormatter.format(new Date(value)),
    },
    {
      title: 'PR',
      key: 'pr',
      render: (_value: unknown, run: AgentRun) =>
        run.pr_url !== null ? (
          <a
            href={run.pr_url}
            target="_blank"
            rel="noreferrer"
            onClick={(event) => {
              event.stopPropagation()
            }}
          >
            View PR
          </a>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
  ]

  return (
    <Flex vertical gap={24} style={{ maxWidth: 1400, margin: '0 auto' }}>
      <Flex align="flex-start" justify="space-between" wrap="wrap" gap={16}>
        <Flex vertical gap={4}>
          <Title level={3} style={{ margin: 0 }}>
            Mission Control
          </Title>
          <Text type="secondary">
            Live feed of AI agent runs (Claude / Codex / Gemini) driving the rails build → gate →
            cross-vendor review → PR loop. {total} run{total === 1 ? '' : 's'} recorded.
          </Text>
        </Flex>
        <Badge
          status="processing"
          color={token.colorPrimary}
          text={
            <Text type="secondary">Polling every {Math.round(POLL_INTERVAL_MS / 1000)}s</Text>
          }
        />
      </Flex>

      {error !== null && (
        <Alert
          type="error"
          showIcon
          closable
          message="Couldn't load agent runs"
          description={error}
        />
      )}

      <Card variant="borderless" style={{ boxShadow: token.boxShadowTertiary }}>
        <Table<AgentRun>
          aria-label="Agent runs"
          rowKey="id"
          columns={columns}
          dataSource={items}
          loading={loading}
          pagination={false}
          onRow={(run) => ({
            onClick: () => {
              setSelectedRun(run)
            },
            style: { cursor: 'pointer' },
          })}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={
                  <>
                    No agent runs yet — run <Text code>uv run rails build-feature "..."</Text> to
                    see one appear here.
                  </>
                }
              />
            ),
          }}
        />
      </Card>

      <RunDetailDrawer
        run={selectedRun}
        onClose={() => {
          setSelectedRun(null)
        }}
      />
    </Flex>
  )
}

export default MissionControlPage
