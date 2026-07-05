import { useEffect, useState } from 'react'
import { App, Button, Drawer, Flex, Form, Input, InputNumber, Select } from 'antd'
import { ApiError, api, type Vehicle } from '../lib/api'

// keep in sync with api/_lib/vehicles.py's VIN_MIN_LEN / VIN_MAX_LEN
const VIN_MIN_LEN = 5
const VIN_MAX_LEN = 20
// keep in sync with api/_lib/vehicles.py's MIN_VEHICLE_YEAR / MAX_VEHICLE_YEAR
const MIN_VEHICLE_YEAR = 1950
const MAX_VEHICLE_YEAR = 2100

const STATUS_OPTIONS: { label: string; value: Vehicle['status'] }[] = [
  { label: 'Available', value: 'available' },
  { label: 'Reserved', value: 'reserved' },
  { label: 'Sold', value: 'sold' },
]

interface VehicleFormValues {
  vin: string
  make: string
  model: string
  year: number
  price: number
  mileage_km: number
  status: Vehicle['status']
}

interface VehicleFormDrawerProps {
  open: boolean
  /** null = create mode; a Vehicle = edit mode for that row. */
  vehicle: Vehicle | null
  onClose: () => void
  /** Called after a successful create/update so the caller can reload the table + stats. */
  refresh: () => void
}

/** Only the fields that actually differ from `original` -- the API rejects PATCH bodies
 * carrying unknown/unchanged-but-still-present fields with extra="forbid" semantics in
 * spirit (status is outright forbidden), so we keep the payload minimal regardless. */
function diffPatch(
  values: VehicleFormValues,
  original: Vehicle,
): Partial<Record<'make' | 'model' | 'year' | 'price_cents' | 'mileage_km', unknown>> {
  const patch: Partial<Record<'make' | 'model' | 'year' | 'price_cents' | 'mileage_km', unknown>> =
    {}
  if (values.make !== original.make) patch.make = values.make
  if (values.model !== original.model) patch.model = values.model
  if (values.year !== original.year) patch.year = values.year
  const priceCents = Math.round(values.price * 100)
  if (priceCents !== original.price_cents) patch.price_cents = priceCents
  if (values.mileage_km !== original.mileage_km) patch.mileage_km = values.mileage_km
  return patch
}

/** Create/edit drawer for a vehicle. Status is only settable at create time --
 * transitions after that go through RowActions, which enforces the server's matrix. */
function VehicleFormDrawer({ open, vehicle, onClose, refresh }: VehicleFormDrawerProps) {
  const { message } = App.useApp()
  const [form] = Form.useForm<VehicleFormValues>()
  const [submitting, setSubmitting] = useState(false)
  const isEdit = vehicle !== null

  useEffect(() => {
    if (!open) return
    if (vehicle) {
      form.setFieldsValue({
        vin: vehicle.vin,
        make: vehicle.make,
        model: vehicle.model,
        year: vehicle.year,
        price: vehicle.price_cents / 100,
        mileage_km: vehicle.mileage_km,
        status: vehicle.status,
      })
    } else {
      form.resetFields()
      form.setFieldsValue({ status: 'available', mileage_km: 0 })
    }
  }, [open, vehicle, form])

  async function handleFinish(values: VehicleFormValues) {
    setSubmitting(true)
    try {
      if (vehicle) {
        const patch = diffPatch(values, vehicle)
        if (Object.keys(patch).length > 0) {
          await api.patch<Vehicle>(`/api/vehicles/${vehicle.id}`, patch)
        }
        message.success('Vehicle updated.')
      } else {
        await api.post<Vehicle>('/api/vehicles', {
          vin: values.vin,
          make: values.make,
          model: values.model,
          year: values.year,
          price_cents: Math.round(values.price * 100),
          mileage_km: values.mileage_km,
          status: values.status,
        })
        message.success('Vehicle created.')
      }
      refresh()
      onClose()
    } catch (err) {
      if (err instanceof ApiError && err.code === 'duplicate_vin') {
        form.setFields([{ name: 'vin', errors: [err.message] }])
      } else if (err instanceof ApiError) {
        message.error(err.message)
      } else {
        message.error('Something went wrong. Please try again.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Drawer
      title={isEdit ? `Edit ${vehicle.make} ${vehicle.model}` : 'New vehicle'}
      open={open}
      onClose={onClose}
      width={420}
      destroyOnHidden
      maskClosable={!submitting}
      keyboard={!submitting}
      footer={
        <Flex justify="end" gap={8}>
          <Button onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button type="primary" loading={submitting} onClick={() => form.submit()}>
            Save
          </Button>
        </Flex>
      }
    >
      <Form<VehicleFormValues>
        form={form}
        layout="vertical"
        disabled={submitting}
        onFinish={(values) => void handleFinish(values)}
      >
        <Form.Item
          label="VIN"
          name="vin"
          rules={[
            { required: true, message: 'VIN is required' },
            {
              min: VIN_MIN_LEN,
              max: VIN_MAX_LEN,
              message: `VIN must be ${VIN_MIN_LEN}-${VIN_MAX_LEN} characters`,
            },
          ]}
        >
          <Input disabled={isEdit} placeholder="e.g. 1HGCM82633A004352" />
        </Form.Item>

        <Form.Item
          label="Make"
          name="make"
          rules={[{ required: true, message: 'Make is required' }]}
        >
          <Input placeholder="e.g. Honda" />
        </Form.Item>

        <Form.Item
          label="Model"
          name="model"
          rules={[{ required: true, message: 'Model is required' }]}
        >
          <Input placeholder="e.g. Accord" />
        </Form.Item>

        <Form.Item
          label="Year"
          name="year"
          rules={[{ required: true, message: 'Year is required' }]}
        >
          <InputNumber
            min={MIN_VEHICLE_YEAR}
            max={MAX_VEHICLE_YEAR}
            precision={0}
            style={{ width: '100%' }}
          />
        </Form.Item>

        <Form.Item
          label="Price"
          name="price"
          rules={[{ required: true, message: 'Price is required' }]}
        >
          <InputNumber min={0} precision={2} prefix="€" style={{ width: '100%' }} />
        </Form.Item>

        <Form.Item
          label="Mileage (km)"
          name="mileage_km"
          rules={[{ required: true, message: 'Mileage is required' }]}
        >
          <InputNumber min={0} precision={0} style={{ width: '100%' }} />
        </Form.Item>

        {!isEdit && (
          <Form.Item label="Status" name="status" rules={[{ required: true }]}>
            <Select options={STATUS_OPTIONS} />
          </Form.Item>
        )}

        {/* Hidden submit button: makes Enter submit the form (the visible Save
            button lives in the Drawer footer, outside the <form> element). */}
        <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
      </Form>
    </Drawer>
  )
}

export default VehicleFormDrawer
