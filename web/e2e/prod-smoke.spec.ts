import { expect, test } from '@playwright/test'

/**
 * Production smoke test: a REAL browser driving the REAL login form against
 * the REAL production deployment and the REAL hosted Supabase database.
 * No session injection, no mocks, no local servers -- this is the
 * demo-realism proof for the Phase-1 freeze.
 *
 * Skipped by default (and therefore absent from the local/CI `npm run e2e`
 * run, which never sets PROD_URL). To run it for real:
 *
 *   PROD_URL=https://nextlane-blond.vercel.app \
 *   REVIEWER_EMAIL=reviewer@nextlane-demo.dev \
 *   REVIEWER_PASSWORD=*** \
 *   npm --prefix web run e2e:prod
 */
test.describe('production smoke', () => {
  test.skip(!process.env.PROD_URL, 'PROD_URL not set -- production smoke only runs on demand')

  test('real login, inventory, golden path, report issue, logout', async ({ page }) => {
    const prodUrl = process.env.PROD_URL
    const email = process.env.REVIEWER_EMAIL
    const password = process.env.REVIEWER_PASSWORD

    if (!prodUrl || !email || !password) {
      throw new Error('PROD_URL, REVIEWER_EMAIL and REVIEWER_PASSWORD must all be set')
    }

    const vin = `TEST-${Date.now()}`
    const reportMessage = `prod-smoke test report ${Date.now()}`

    await test.step('real login against production', async () => {
      await page.goto(prodUrl)
      await expect(page).toHaveURL(/\/login/)

      await page.getByLabel('Email').fill(email)
      await page.getByLabel('Password').fill(password)
      await page.getByRole('button', { name: 'Sign in' }).click()

      await expect(page).not.toHaveURL(/\/login/)
      await expect(page.getByRole('heading', { name: 'Inventory' })).toBeVisible()
    })

    await test.step('inventory renders the seeded vehicles', async () => {
      const table = page.getByRole('table', { name: 'Vehicle inventory' })
      await expect(table).toBeVisible()

      // Seeded row (supabase/seed.sql): Renault Clio, VIN starts VF1.
      const search = page.getByRole('searchbox', { name: 'Search inventory' })
      await search.fill('VF1HBUSRJGF4CBFPR')
      await search.press('Enter')

      const seedRow = page.getByRole('row', { name: /VF1HBUSRJGF4CBFPR/ })
      await expect(seedRow).toHaveCount(1)
      await expect(seedRow.getByText('Renault')).toBeVisible()

      await search.fill('')
      await search.press('Enter')
    })

    const row = page.getByRole('row', { name: new RegExp(vin) })

    await test.step('create a vehicle', async () => {
      await page.getByRole('button', { name: 'New vehicle' }).click()

      const dialog = page.getByRole('dialog')
      await expect(dialog).toBeVisible()

      await dialog.getByLabel('VIN').fill(vin)
      await dialog.getByLabel('Make').fill('ProdSmoke')
      await dialog.getByLabel('Model').fill('Verify')
      await dialog.getByLabel('Year').fill('2024')
      await dialog.getByLabel('Price').fill('9999.00')
      await dialog.getByLabel('Mileage (km)').fill('1')

      await dialog.getByRole('button', { name: 'Save' }).click()

      await expect(page.getByText('Vehicle created.')).toBeVisible()
      await expect(dialog).not.toBeVisible()
    })

    await test.step('search isolates the new vehicle', async () => {
      const search = page.getByRole('searchbox', { name: 'Search inventory' })
      await search.fill(vin)
      await search.press('Enter')

      await expect(page.getByRole('row', { name: new RegExp(vin) })).toHaveCount(1)
      await expect(row.getByText('ProdSmoke')).toBeVisible()
      await expect(row.getByText('Available', { exact: true })).toBeVisible()
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
      await page.getByRole('menuitem', { name: 'Delete', exact: true }).click()
      await page.getByRole('button', { name: 'Confirm' }).click()

      await expect(page.getByText('Vehicle deleted.')).toBeVisible()
      await expect(page.getByRole('row', { name: new RegExp(vin) })).toHaveCount(0)
    })

    await test.step('report an issue', async () => {
      await page.getByRole('button', { name: 'Report issue' }).click()

      const dialog = page.getByRole('dialog', { name: 'Report an issue' })
      await expect(dialog).toBeVisible()

      await dialog.getByLabel('What went wrong?').fill(reportMessage)
      await dialog.getByRole('button', { name: 'Send report' }).click()

      await expect(page.getByText('Thanks — your report was sent.')).toBeVisible()
      await expect(dialog).not.toBeVisible()
    })

    await test.step('log out', async () => {
      await page.getByRole('button', { name: 'Log out' }).click()
      await expect(page).toHaveURL(/\/login/)
    })
  })
})
