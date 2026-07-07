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
    // Make is a dropdown of the curated top-30 makes (GET /api/vehicles/makes);
    // options render in a portal outside the dialog.
    await dialog.getByLabel('Make').click()
    await page.getByRole('option', { name: 'Toyota', exact: true }).click()
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
    await expect(row.getByText('Toyota')).toBeVisible()
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

  // Row actions live behind a per-row "⋯" overflow menu (aria-label
  // "Row actions"); status changes + delete confirm through a dialog.
  await test.step('reserve the vehicle', async () => {
    await row.getByRole('button', { name: 'Row actions' }).click()
    await page.getByRole('menuitem', { name: 'Reserve', exact: true }).click()
    await page.getByRole('button', { name: 'Confirm' }).click()

    await expect(page.getByText('Vehicle reserved.')).toBeVisible()
    await expect(row.getByText('Reserved', { exact: true })).toBeVisible()
  })

  await test.step('mark the vehicle sold', async () => {
    await row.getByRole('button', { name: 'Row actions' }).click()
    await page.getByRole('menuitem', { name: 'Mark sold', exact: true }).click()
    await page.getByRole('button', { name: 'Confirm' }).click()

    await expect(page.getByText('Vehicle marked as sold.')).toBeVisible()
    await expect(row.getByText('Sold', { exact: true })).toBeVisible()
  })

  await test.step('delete the vehicle', async () => {
    await row.getByRole('button', { name: 'Row actions' }).click()

    // Sold is terminal: the menu offers no status transitions, only Edit + Delete.
    await expect(page.getByRole('menuitem', { name: 'Reserve', exact: true })).toHaveCount(0)
    await expect(page.getByRole('menuitem', { name: 'Mark sold', exact: true })).toHaveCount(0)
    await expect(page.getByRole('menuitem', { name: 'Cancel reservation' })).toHaveCount(0)

    await page.getByRole('menuitem', { name: 'Delete', exact: true }).click()
    await page.getByRole('button', { name: 'Confirm' }).click()

    await expect(page.getByText('Vehicle deleted.')).toBeVisible()
    await expect(page.getByRole('row', { name: new RegExp(vin) })).toHaveCount(0)
    await expect(
      page.getByText('No vehicles match your search or filters. Try widening your criteria.'),
    ).toBeVisible()
  })
})
