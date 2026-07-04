import { expect, test } from '@playwright/test'
import { injectSession } from './auth'

/**
 * Golden path smoke test: real browser, real API, real Postgres. The only
 * faked thing is the auth session (a test-signed ES256 JWT verified by the
 * API against a local JWKS -- see global-setup.ts / auth.ts).
 *
 * One vehicle is created with a unique VIN and used to drive create -> search
 * -> reserve -> sell -> delete, isolating this run from anything already in
 * the table (seeded or otherwise).
 */
test('inventory golden path: create, search, transition, delete', async ({ page }) => {
  const vin = `TEST-${Date.now()}`

  await test.step('authenticated session lands on the inventory page', async () => {
    await injectSession(page)
    await page.goto('/')

    await expect(page).not.toHaveURL(/\/login/)
    await expect(page.getByRole('heading', { name: 'Inventory' })).toBeVisible()
    await expect(page.getByRole('table', { name: 'Vehicle inventory' })).toBeVisible()
  })

  await test.step('create a vehicle', async () => {
    await page.getByRole('button', { name: 'New vehicle' }).click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()

    await dialog.getByLabel('VIN').fill(vin)
    await dialog.getByLabel('Make').fill('Playwright')
    await dialog.getByLabel('Model').fill('Smoke')
    await dialog.getByLabel('Year').fill('2024')
    await dialog.getByLabel('Price').fill('12345.67')
    await dialog.getByLabel('Mileage (km)').fill('10')

    await dialog.getByRole('button', { name: 'Save' }).click()

    await expect(page.getByText('Vehicle created.')).toBeVisible()
    await expect(dialog).not.toBeVisible()
  })

  const row = page.getByRole('row', { name: new RegExp(vin) })
  const totalText = page.locator('.ant-pagination-total-text')
  let fullInventoryTotal = ''

  await test.step('search isolates the new vehicle', async () => {
    const search = page.getByRole('searchbox', { name: 'Search inventory' })
    await expect(totalText).toHaveText(/\d+ vehicles?/)
    fullInventoryTotal = await totalText.textContent().then((text) => text ?? '')

    await search.fill(vin)
    await search.press('Enter')

    await expect(page.getByRole('row', { name: new RegExp(vin) })).toHaveCount(1)
    await expect(row.getByText('Playwright')).toBeVisible()
    await expect(row.getByText('Smoke')).toBeVisible()
    await expect(row.getByText(/12\.345,67\s*€/)).toBeVisible()
    await expect(row.getByText('Available', { exact: true })).toBeVisible()
  })

  await test.step('clear filters restores the full vehicle list', async () => {
    const search = page.getByRole('searchbox', { name: 'Search inventory' })

    await page.getByRole('button', { name: 'Clear filters' }).click()

    await expect(search).toHaveValue('')
    await expect(totalText).toHaveText(fullInventoryTotal)
    await expect(page.getByText('No vehicles match your search or filters.')).toHaveCount(0)

    await search.fill(vin)
    await search.press('Enter')
    await expect(page.getByRole('row', { name: new RegExp(vin) })).toHaveCount(1)
  })

  await test.step('reserve the vehicle', async () => {
    await row.getByRole('button', { name: 'Reserve' }).click()
    await page.getByRole('button', { name: 'Confirm' }).click()

    await expect(page.getByText('Vehicle reserved.')).toBeVisible()
    await expect(row.getByText('Reserved', { exact: true })).toBeVisible()
  })

  await test.step('mark the vehicle sold', async () => {
    await row.getByRole('button', { name: 'Mark sold' }).click()
    await page.getByRole('button', { name: 'Confirm' }).click()

    await expect(page.getByText('Vehicle marked as sold.')).toBeVisible()
    await expect(row.getByText('Sold', { exact: true })).toBeVisible()

    // Sold is terminal: no transition actions left, just the "—" placeholder.
    await expect(row.getByRole('button', { name: 'Reserve' })).toHaveCount(0)
    await expect(row.getByRole('button', { name: 'Mark sold' })).toHaveCount(0)
    await expect(row.getByRole('button', { name: 'Cancel reservation' })).toHaveCount(0)
    await expect(row.getByText('—', { exact: true })).toBeVisible()
  })

  await test.step('delete the vehicle', async () => {
    await row.getByRole('button', { name: 'Delete' }).click()
    await page.getByRole('button', { name: 'Confirm' }).click()

    await expect(page.getByText('Vehicle deleted.')).toBeVisible()
    await expect(page.getByRole('row', { name: new RegExp(vin) })).toHaveCount(0)
    await expect(
      page.getByText('No vehicles match your search or filters. Try widening your criteria.'),
    ).toBeVisible()
  })
})
