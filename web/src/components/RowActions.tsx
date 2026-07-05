import { useState } from 'react'
import { App, Button, Dropdown } from 'antd'
import type { MenuProps } from 'antd'
import { ApiError, api, type Vehicle } from '../lib/api'

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

interface RowActionsProps {
  vehicle: Vehicle
  /** Opens the edit drawer for this row. */
  onEdit: (vehicle: Vehicle) => void
  /** Called after a successful transition/delete so the table + stats reload. */
  refresh: () => void
}

/**
 * Per-row overflow menu: Edit, the legal status transitions for the row's
 * current status, and Delete. Collapsed behind a single "⋯" trigger so the
 * actions column stays compact -- a row of inline buttons overflowed the
 * fixed-right column once labels like "Cancel reservation" were present.
 * Status and delete go through a confirm dialog (destructive / state-changing);
 * the dialog's "Confirm" button is the same affordance the previous inline
 * Popconfirms used.
 */
function RowActions({ vehicle, onEdit, refresh }: RowActionsProps) {
  const { message, modal } = App.useApp()
  const [busy, setBusy] = useState(false)

  async function transition(next: Status) {
    setBusy(true)
    try {
      await api.post<Vehicle>(`/api/vehicles/${vehicle.id}/status`, { status: next })
      message.success(SUCCESS_MESSAGE[next])
      refresh()
    } catch (err) {
      message.error(err instanceof ApiError ? err.message : 'Failed to update status.')
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    setBusy(true)
    try {
      await api.del(`/api/vehicles/${vehicle.id}`)
      message.success('Vehicle deleted.')
      refresh()
    } catch (err) {
      message.error(err instanceof ApiError ? err.message : 'Failed to delete vehicle.')
    } finally {
      setBusy(false)
    }
  }

  const items: MenuProps['items'] = [
    { key: 'edit', label: 'Edit', onClick: () => onEdit(vehicle) },
    ...TRANSITIONS[vehicle.status].map((next) => ({
      key: `status:${next}`,
      label: ACTION_LABEL[next],
      onClick: () => {
        void modal.confirm({
          title: `${ACTION_LABEL[next]}?`,
          okText: 'Confirm',
          onOk: () => transition(next),
        })
      },
    })),
    { type: 'divider' as const },
    {
      key: 'delete',
      label: 'Delete',
      danger: true,
      onClick: () => {
        void modal.confirm({
          title: `Delete ${vehicle.make} ${vehicle.model}?`,
          content: 'This permanently removes the vehicle.',
          okText: 'Confirm',
          okButtonProps: { danger: true },
          onOk: () => remove(),
        })
      },
    },
  ]

  return (
    <Dropdown menu={{ items }} trigger={['click']} placement="bottomRight">
      <Button size="small" aria-label="Row actions" loading={busy}>
        &#8943;
      </Button>
    </Dropdown>
  )
}

export default RowActions
