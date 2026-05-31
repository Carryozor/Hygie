// frontend/vue/e2e/navigation.spec.js
import { test, expect } from '@playwright/test'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

// Use saved auth state
test.use({ storageState: path.join(__dirname, '.auth/state.json') })

test.describe('Authenticated navigation', () => {
  test('dashboard shows sidebar', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('aside')).toBeVisible()
    await expect(page.locator('aside svg').first()).toBeVisible()
  })

  test('navigates to rules page', async ({ page }) => {
    await page.goto('/')
    await page.click('a[href="/rules"]')
    await expect(page).toHaveURL('/rules')
  })

  test('navigates to settings page', async ({ page }) => {
    await page.goto('/')
    await page.click('a[href="/settings"]')
    await expect(page).toHaveURL('/settings')
  })

  test('navigates to logs page', async ({ page }) => {
    await page.goto('/')
    await page.click('a[href="/logs"]')
    await expect(page).toHaveURL('/logs')
  })
})
