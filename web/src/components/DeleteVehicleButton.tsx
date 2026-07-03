import { useState } from 'react'
import { App, Button, Popconfirm } from 'antd'
import { ApiError, api, type Vehicle } from '../lib/api'

interface DeleteVehicleButtonProps {
  vehicle: Vehicle
  /** Called after a successful delete so the caller can reload the table + stats. */
  refresh: () => void
}

/** Per-row delete button -- the only removal path, available for every status
 * (sold vehicles can't transition, so this is how they leave the lot). */
function DeleteVehicleButton({ vehicle, refresh }: DeleteVehicleButtonProps) {
  const { message } = App.useApp()
  const [pending, setPending] = useState(false)

  async function handleDelete() {
    setPending(true)
    try {
      await api.del(`/api/vehicles/${vehicle.id}`)
      message.success('Vehicle deleted.')
      refresh()
    } catch (err) {
      message.error(err instanceof ApiError ? err.message : 'Failed to delete vehicle.')
    } finally {
      setPending(false)
    }
  }

  return (
    <Popconfirm
      title={`Delete ${vehicle.make} ${vehicle.model}?`}
      description="This permanently removes the vehicle."
      okText="Confirm"
      okButtonProps={{ danger: true }}
      onConfirm={() => void handleDelete()}
    >
      <Button size="small" danger loading={pending}>
        Delete
      </Button>
    </Popconfirm>
  )
}

export default DeleteVehicleButton
