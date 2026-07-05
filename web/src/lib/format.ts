/** Shared date/time locale for every `Intl.DateTimeFormat` in the app, so
 * Mission Control and Inventory render timestamps identically. */
export const DATE_LOCALE = 'en-GB'

const costFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
})

/** Format a rails-run USD cost consistently across Mission Control -- the runs
 * list and the detail drawer share this so they never diverge. */
export function formatCostUsd(cost: number): string {
  return costFormatter.format(cost)
}
