import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'
import { Alert, Button, Descriptions, Drawer, Empty, Spin, Tag, Timeline, Typography, theme } from 'antd'
import { ApiError, api, type AgentRun, type RunDetail, type RunStepStatus } from '../lib/api'
import { formatCostUsd } from '../lib/format'
import MarkdownLite from './MarkdownLite'

const { Text } = Typography

// Step details longer than either threshold collapse behind a "Show more"
// toggle so the timeline stays scannable; both dimensions are in px.
const DETAIL_COLLAPSE_MIN_CHARS = 240
const DETAIL_COLLAPSE_MIN_LINES = 5
const DETAIL_COLLAPSED_MAX_HEIGHT = 120
const DETAIL_FADE_HEIGHT = 36

interface RunDetailDrawerProps {
  /** null = closed. A run row = fetch and show that run's step timeline. */
  run: AgentRun | null
  onClose: () => void
}

/** A step's detail rendered as markdown, with collapse/expand for long agent
 * summaries so the timeline stays scannable by default. Short details (e.g.
 * "6/6 green") render inline with no toggle. */
function StepDetail({ text }: { text: string }) {
  const { token } = theme.useToken()
  const [expanded, setExpanded] = useState(false)
  const isLong =
    text.length > DETAIL_COLLAPSE_MIN_CHARS ||
    text.split('\n').length > DETAIL_COLLAPSE_MIN_LINES

  if (!isLong) return <MarkdownLite text={text} />

  return (
    <div>
      <div
        style={{
          maxHeight: expanded ? undefined : DETAIL_COLLAPSED_MAX_HEIGHT,
          overflow: 'hidden',
          position: 'relative',
        }}
      >
        <MarkdownLite text={text} />
        {!expanded && (
          <div
            aria-hidden
            style={{
              position: 'absolute',
              left: 0,
              right: 0,
              bottom: 0,
              height: DETAIL_FADE_HEIGHT,
              background: `linear-gradient(transparent, ${token.colorBgElevated})`,
            }}
          />
        )}
      </div>
      <Button
        type="link"
        size="small"
        style={{ padding: 0, height: 'auto' }}
        onClick={() => {
          setExpanded((v) => !v)
        }}
      >
        {expanded ? 'Show less' : 'Show more'}
      </Button>
    </div>
  )
}

/** Read-only drawer: GET /api/runs/{id} on open, rendered as a step timeline
 * (phase + status + timestamp), newest-progress-last (the API already
 * returns steps ordered by seq asc). */
function RunDetailDrawer({ run, onClose }: RunDetailDrawerProps) {
  const { token } = theme.useToken()
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (run === null) {
      setDetail(null)
      setError(null)
      return
    }
    let active = true
    const controller = new AbortController()

    setLoading(true)
    setError(null)
    api
      .get<RunDetail>(`/api/runs/${run.id}`, undefined, controller.signal)
      .then((res) => {
        if (!active) return
        setDetail(res)
        setLoading(false)
      })
      .catch((err: unknown) => {
        if (!active) return
        setError(err instanceof ApiError ? err.message : 'Failed to load this run.')
        setLoading(false)
      })

    return () => {
      active = false
      controller.abort()
    }
  }, [run])

  const dotColor: Record<RunStepStatus, string> = {
    started: token.colorInfo,
    ok: token.colorSuccess,
    failed: token.colorError,
  }
  const tagStyle: Record<RunStepStatus, CSSProperties> = {
    started: {
      color: token.colorInfo,
      background: token.colorInfoBg,
      borderColor: token.colorInfoBorder,
    },
    ok: {
      color: token.colorSuccess,
      background: token.colorSuccessBg,
      borderColor: token.colorSuccessBorder,
    },
    failed: {
      color: token.colorError,
      background: token.colorErrorBg,
      borderColor: token.colorErrorBorder,
    },
  }

  return (
    <Drawer title={run?.task_summary ?? ''} open={run !== null} onClose={onClose} width={480}>
      {run && (
        <>
          <Descriptions column={1} size="small" style={{ marginBottom: 24 }}>
            <Descriptions.Item label="Task kind">{run.task_kind}</Descriptions.Item>
            <Descriptions.Item label="Engine">
              {run.engine}
              {run.reviewer_engine ? ` → ${run.reviewer_engine}` : ''}
            </Descriptions.Item>
            <Descriptions.Item label="Branch">
              <Text code>{run.worktree_branch ?? '—'}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="Retries">{run.retries}</Descriptions.Item>
            <Descriptions.Item label="Cost">
              {run.cost_usd !== null ? formatCostUsd(run.cost_usd) : '—'}
            </Descriptions.Item>
            {run.pr_url !== null && (
              <Descriptions.Item label="Pull request">
                <a href={run.pr_url} target="_blank" rel="noreferrer">
                  {run.pr_url}
                </a>
              </Descriptions.Item>
            )}
          </Descriptions>

          <Text strong>Step timeline</Text>
          {loading && <Spin style={{ display: 'block', marginTop: 16 }} />}
          {error !== null && (
            <Alert type="error" showIcon message={error} style={{ marginTop: 12 }} />
          )}
          {!loading && detail !== null && detail.steps.length === 0 && (
            <Empty description="No steps recorded yet." style={{ marginTop: 16 }} />
          )}
          {!loading && detail !== null && detail.steps.length > 0 && (
            <Timeline
              style={{ marginTop: 16 }}
              items={detail.steps.map((step) => ({
                key: step.id,
                color: dotColor[step.status],
                children: (
                  <>
                    <Text strong>{step.phase}</Text>{' '}
                    <Tag style={tagStyle[step.status]}>{step.status}</Tag>
                    <br />
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {new Date(step.at).toLocaleString()}
                    </Text>
                    {step.detail !== null && <StepDetail text={step.detail} />}
                  </>
                ),
              }))}
            />
          )}
        </>
      )}
    </Drawer>
  )
}

export default RunDetailDrawer
