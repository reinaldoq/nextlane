import { useState } from 'react'
import { App, Button, Popconfirm, Space, Typography } from 'antd'
import { ApiError, api, type Vehicle } from '../lib/api'

const { Text } = Typography

type Status = Vehicle['status']

// Mirrors api/_lib/transitions.py's ALLOWED matrix -- keep these in sync.
const TRANSITIONS: Record<Status, Status[]> = {
  available: ['reserved', 'sold'],
  reserved: ['available', 'sold'],
  sold: [],
}

// Labelled by the *target* status of the transition.
const ACTION_LABEL: Record<Status, string> = {
  available: 'Cancel reservation',
  reserved: 'Reserve',
  sold: 'Mark sold',
}

// Success toast per *target* status of the completed transition.
const SUCCESS_MESSAGE: Record<Status, string> = {
  available: 'Reservation cancelled.',
  reserved: 'Vehicle reserved.',
  sold: 'Vehicle marked as sold.',
}

interface StatusActionsProps {
  vehicle: Vehicle
  /** Called after a successful transition so the caller can reload the table + stats. */
  refresh: () => void
}

/** Per-row status transition buttons, restricted to legal next states for the row's current status. */
function StatusActions({ vehicle, refresh }: StatusActionsProps) {
  const { message } = App.useApp()
  const [pending, setPending] = useState<Status | null>(null)

  const nextStates = TRANSITIONS[vehicle.status]

  if (nextStates.length === 0) {
    return <Text type="secondary">—</Text>
  }

  async function transition(next: Status) {
    setPending(next)
    try {
      await api.post<Vehicle>(`/api/vehicles/${vehicle.id}/status`, { status: next })
      message.success(SUCCESS_MESSAGE[next])
      refresh()
    } catch (err) {
      message.error(err instanceof ApiError ? err.message : 'Failed to update status.')
    } finally {
      setPending(null)
    }
  }

  return (
    <Space size="small">
      {nextStates.map((next) => (
        <Popconfirm
          key={next}
          title={`${ACTION_LABEL[next]}?`}
          okText="Confirm"
          onConfirm={() => void transition(next)}
        >
          <Button
            size="small"
            loading={pending === next}
            disabled={pending !== null && pending !== next}
          >
            {ACTION_LABEL[next]}
          </Button>
        </Popconfirm>
      ))}
    </Space>
  )
}

export default StatusActions
