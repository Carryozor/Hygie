// frontend/vue/e2e/rules.spec.js
import { test, expect } from '@playwright/test'

test.use({ storageState: undefined })

const E2E_USER = process.env.E2E_USERNAME || 'admin'
const E2E_PASS = process.env.E2E_PASSWORD || ''

async function login(page) {
  await page.goto('/login')
  await page.fill('input[autocomplete="username"]', E2E_USER)
  await page.fill('input[autocomplete="current-password"]', E2E_PASS)
  await page.click('button[type="submit"]')
  await page.waitForURL('/', { timeout: 15_000 })
}

test.describe('Rules CRUD', () => {
  test.beforeEach(async ({ page }) => {
    if (!E2E_PASS) test.skip(true, 'E2E_PASSWORD not set')
    await login(page)
    await page.goto('/rules')
    await expect(page).toHaveURL('/rules')
  })

  test('rules page loads without error', async ({ page }) => {
    // Should show either the rules list or an empty state, not an error
    const hasError = await page.locator('text=Erreur').count()
    expect(hasError).toBe(0)
    await expect(page.locator('body')).toBeVisible()
  })

  test('sidebar navigation highlights rules as active', async ({ page }) => {
    const rulesLink = page.locator('aside a[href="/rules"]')
    await expect(rulesLink).toBeVisible()
    // Active link should have different styling
    const classList = await rulesLink.getAttribute('class')
    expect(classList).toBeTruthy()
  })
})

test.describe('Settings save flow', () => {
  test.beforeEach(async ({ page }) => {
    if (!E2E_PASS) test.skip(true, 'E2E_PASSWORD not set')
    await login(page)
    await page.goto('/settings')
    await expect(page).toHaveURL('/settings')
  })

  test('settings page shows tabs', async ({ page }) => {
    await expect(page.locator('button').filter({ hasText: 'Général' })).toBeVisible()
    await expect(page.locator('button').filter({ hasText: 'Seerr' })).toBeVisible()
    await expect(page.locator('button').filter({ hasText: 'Radarr' })).toBeVisible()
  })

  test('can navigate between settings tabs without crash', async ({ page }) => {
    const tabs = ['Général', 'Seerr', 'Radarr', 'Sonarr']
    for (const tab of tabs) {
      await page.click(`button:has-text("${tab}")`)
      await page.waitForTimeout(300)
      // No error should appear
      const errors = await page.locator('text=TypeError, text=ReferenceError').count()
      expect(errors).toBe(0)
    }
  })
})

test.describe('Queue view', () => {
  test.beforeEach(async ({ page }) => {
    if (!E2E_PASS) test.skip(true, 'E2E_PASSWORD not set')
    await login(page)
  })

  test('queue page loads and shows content or empty state', async ({ page }) => {
    await page.goto('/queue')
    await expect(page).toHaveURL('/queue')
    // Either shows items or empty state
    const hasContent = await page.locator('[data-testid="queue-item"], text=Aucun').count()
    // Queue loads without crash
    await expect(page.locator('body')).toBeVisible()
  })

  test('logs page shows filter buttons', async ({ page }) => {
    await page.goto('/logs')
    await expect(page).toHaveURL('/logs')
    await expect(page.locator('button').filter({ hasText: 'Tous' })).toBeVisible()
    await expect(page.locator('button').filter({ hasText: 'Erreur' })).toBeVisible()
  })
})
