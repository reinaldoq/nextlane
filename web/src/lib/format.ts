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
