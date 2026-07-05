import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Empty,
  Flex,
  Input,
  Segmented,
  Table,
  Tag,
  Typography,
  theme,
} from 'antd'
import type { TableColumnsType, TableProps } from 'antd'
import RowActions from '../components/RowActions'
import StatCards from '../components/StatCards'
import VehicleFormDrawer from '../components/VehicleFormDrawer'
import {
  DEFAULT_PAGE_SIZE,
  DEFAULT_SORT,
  PAGE_SIZE_OPTIONS,
  SORT_FIELDS,
  useVehicleList,
  type SortField,
  type SortState,
} from '../hooks/useVehicleList'
import type { Vehicle } from '../lib/api'

const { Title, Text } = Typography

type StatusOption = 'all' | Vehicle['status']

const STATUS_OPTIONS: { label: string; value: StatusOption }[] = [
  { label: 'All', value: 'all' },
  { label: 'Available', value: 'available' },
  { label: 'Reserved', value: 'reserved' },
  { label: 'Sold', value: 'sold' },
]

const STATUS_TAG_COLOR: Record<Vehicle['status'], string> = {
  available: 'green',
  reserved: 'gold',
  sold: 'default',
}

const STATUS_LABEL: Record<Vehicle['status'], string> = {
  available: 'Available',
  reserved: 'Reserved',
  sold: 'Sold',
}

const currencyFormatter = new Intl.NumberFormat('de-DE', {
  style: 'currency',
  currency: 'EUR',
})
const mileageFormatter = new Intl.NumberFormat('de-DE')
const updatedFormatter = new Intl.DateTimeFormat('en-GB', {
  dateStyle: 'medium',
  timeStyle: 'short',
})

const SEARCH_DEBOUNCE_MS = 300

function sortOrderFor(field: SortField, sort: SortState): 'ascend' | 'descend' | undefined {
  if (sort.field !== field) return undefined
  return sort.order === 'asc' ? 'ascend' : 'descend'
}

function isSortField(value: unknown): value is SortField {
  return typeof value === 'string' && (SORT_FIELDS as readonly string[]).includes(value)
}

type TableChangeHandler = NonNullable<TableProps<Vehicle>['onChange']>

/** Inventory table: server-driven search, status filter, column sort and pagination. */
function InventoryPage() {
  const { token } = theme.useToken()

  const [rawQuery, setRawQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusOption>('all')
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [refreshKey, setRefreshKey] = useState(0)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [editingVehicle, setEditingVehicle] = useState<Vehicle | null>(null)

  const searchTimeoutRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  // Stable identity: passed down to RowActions/VehicleFormDrawer, and used as
  // a memoized columns dependency below.
  const refresh = useCallback(() => {
    setRefreshKey((key) => key + 1)
  }, [])

  const openCreateDrawer = useCallback(() => {
    setEditingVehicle(null)
    setDrawerOpen(true)
  }, [])

  const openEditDrawer = useCallback((vehicle: Vehicle) => {
    setEditingVehicle(vehicle)
    setDrawerOpen(true)
  }, [])

  const closeDrawer = useCallback(() => {
    setDrawerOpen(false)
  }, [])

  useEffect(
    () => () => {
      clearTimeout(searchTimeoutRef.current)
    },
    [],
  )

  function commitSearch(value: string) {
    clearTimeout(searchTimeoutRef.current)
    setDebouncedQuery(value)
    setPage(1)
  }

  function handleSearchInput(value: string) {
    setRawQuery(value)
    clearTimeout(searchTimeoutRef.current)
    searchTimeoutRef.current = setTimeout(() => {
      commitSearch(value)
    }, SEARCH_DEBOUNCE_MS)
  }

  function handleStatusChange(value: StatusOption) {
    setStatusFilter(value)
    setPage(1)
  }

  function clearFilters() {
    clearTimeout(searchTimeoutRef.current)
    setRawQuery('')
    setDebouncedQuery('')
    setStatusFilter('all')
    setSort(DEFAULT_SORT)
    setPage(1)
  }

  const handleTableChange: TableChangeHandler = (pagination, _filters, sorter, extra) => {
    if (extra.action === 'sort') {
      const single = Array.isArray(sorter) ? sorter[0] : sorter
      if (single?.order && isSortField(single.field)) {
        setSort({
          field: single.field,
          order: single.order === 'ascend' ? 'asc' : 'desc',
        })
      } else {
        // Cleared sort or a column key outside the server whitelist: back to default.
        setSort(DEFAULT_SORT)
      }
      setPage(1)
      return
    }

    setPage(pagination.current ?? 1)
    setPageSize(pagination.pageSize ?? DEFAULT_PAGE_SIZE)
  }

  const { items, total, loading, error } = useVehicleList({
    q: debouncedQuery,
    status: statusFilter === 'all' ? undefined : statusFilter,
    sort,
    page,
    pageSize,
    refreshKey,
  })

  const hasActiveFilters =
    rawQuery.trim() !== '' ||
    debouncedQuery.trim() !== '' ||
    statusFilter !== 'all' ||
    sort.field !== DEFAULT_SORT.field ||
    sort.order !== DEFAULT_SORT.order

  const columns: TableColumnsType<Vehicle> = useMemo(
    () => [
      {
        title: 'VIN',
        dataIndex: 'vin',
        key: 'vin',
        render: (vin: string) => <Text code>{vin}</Text>,
      },
      { title: 'Make', dataIndex: 'make', key: 'make' },
      { title: 'Model', dataIndex: 'model', key: 'model' },
      {
        title: 'Year',
        dataIndex: 'year',
        key: 'year',
        align: 'right',
        sorter: true,
        sortOrder: sortOrderFor('year', sort),
      },
      {
        title: 'Price',
        dataIndex: 'price_cents',
        key: 'price_cents',
        align: 'right',
        sorter: true,
        sortOrder: sortOrderFor('price_cents', sort),
        render: (cents: number) => currencyFormatter.format(cents / 100),
      },
      {
        title: 'Mileage',
        dataIndex: 'mileage_km',
        key: 'mileage_km',
        align: 'right',
        sorter: true,
        sortOrder: sortOrderFor('mileage_km', sort),
        render: (km: number) => `${mileageFormatter.format(km)} km`,
      },
      {
        title: 'Status',
        dataIndex: 'status',
        key: 'status',
        render: (status: Vehicle['status']) => (
          <Tag color={STATUS_TAG_COLOR[status]}>{STATUS_LABEL[status]}</Tag>
        ),
      },
      {
        title: 'Updated',
        dataIndex: 'updated_at',
        key: 'updated_at',
        render: (value: string) => updatedFormatter.format(new Date(value)),
      },
      {
        title: 'Actions',
        key: 'actions',
        fixed: 'right',
        width: 96,
        align: 'center',
        render: (_value: unknown, vehicle: Vehicle) => (
          <RowActions vehicle={vehicle} onEdit={openEditDrawer} refresh={refresh} />
        ),
      },
    ],
    [sort, openEditDrawer, refresh],
  )

  return (
    <Flex vertical gap={24} style={{ maxWidth: 1400, margin: '0 auto' }}>
      <Flex align="flex-start" justify="space-between" wrap="wrap" gap={16}>
        <Flex vertical gap={4}>
          <Title level={3} style={{ margin: 0 }}>
            Inventory
          </Title>
          <Text type="secondary">Search, filter and track every vehicle on the lot.</Text>
        </Flex>
        <Flex gap={8}>
          <Button onClick={refresh}>Refresh</Button>
          <Button type="primary" onClick={openCreateDrawer}>
            New vehicle
          </Button>
        </Flex>
      </Flex>

      <StatCards refreshKey={refreshKey} />

      <Card variant="borderless" style={{ boxShadow: token.boxShadowTertiary }}>
        <Flex vertical gap={16}>
          <Flex align="center" justify="space-between" wrap="wrap" gap={12}>
            <Input.Search
              allowClear
              aria-label="Search inventory"
              placeholder="Search by make, model or VIN"
              value={rawQuery}
              onChange={(event) => {
                handleSearchInput(event.target.value)
              }}
              onSearch={(value) => {
                commitSearch(value)
              }}
              style={{ maxWidth: 360, width: '100%' }}
            />
            <Flex align="center" gap={8} wrap="wrap">
              <Segmented<StatusOption>
                value={statusFilter}
                onChange={handleStatusChange}
                options={STATUS_OPTIONS}
              />
              <Button disabled={!hasActiveFilters} onClick={clearFilters}>
                Clear filters
              </Button>
            </Flex>
          </Flex>

          {error !== null && (
            <Alert
              type="error"
              showIcon
              closable
              message="Couldn't load vehicles"
              description={error}
            />
          )}

          <Table<Vehicle>
            aria-label="Vehicle inventory"
            rowKey="id"
            columns={columns}
            dataSource={items}
            loading={loading}
            onChange={handleTableChange}
            scroll={{ x: 'max-content' }}
            pagination={{
              current: page,
              pageSize,
              total,
              showSizeChanger: true,
              pageSizeOptions: PAGE_SIZE_OPTIONS,
              showTotal: (count) => `${count} vehicle${count === 1 ? '' : 's'}`,
            }}
            locale={{
              emptyText: (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={
                    hasActiveFilters
                      ? 'No vehicles match your search or filters. Try widening your criteria.'
                      : "No vehicles yet. Once inventory is added, it'll show up here."
                  }
                />
              ),
            }}
          />
        </Flex>
      </Card>

      <VehicleFormDrawer
        open={drawerOpen}
        vehicle={editingVehicle}
        onClose={closeDrawer}
        refresh={refresh}
      />
    </Flex>
  )
}

export default InventoryPage
