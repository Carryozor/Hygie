// frontend/vue/e2e/navigation.spec.js
import { test, expect } from '@playwright/test'

// Override auth state — log in directly in each test
test.use({ storageState: undefined })

const E2E_USER = process.env.E2E_USERNAME || 'admin'
const E2E_PASS = process.env.E2E_PASSWORD || ''

// Skip navigation tests if no password provided
test.beforeEach(async ({ page }) => {
  if (!E2E_PASS) {
    test.skip(true, 'E2E_PASSWORD not set — skipping navigation tests')
  }
  // Login before each test
  await page.goto('/login')
  await page.fill('input[autocomplete="username"]', E2E_USER)
  await page.fill('input[autocomplete="current-password"]', E2E_PASS)
  await page.click('button[type="submit"]')
  await page.waitForURL('/', { timeout: 15_000 })
})

test.describe('Authenticated navigation', () => {
  test('dashboard shows sidebar', async ({ page }) => {
    await expect(page.locator('aside')).toBeVisible()
    await expect(page.locator('aside svg').first()).toBeVisible()
  })

  test('navigates to rules page', async ({ page }) => {
    await page.click('a[href="/rules"]')
    await expect(page).toHaveURL('/rules')
  })

  test('navigates to settings page', async ({ page }) => {
    await page.click('a[href="/settings"]')
    await expect(page).toHaveURL('/settings')
  })

  test('navigates to logs page', async ({ page }) => {
    await page.click('a[href="/logs"]')
    await expect(page).toHaveURL('/logs')
  })
})
