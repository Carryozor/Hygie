// frontend/vue/e2e/login.spec.js
import { test, expect } from '@playwright/test'

test.use({ storageState: undefined })

test.describe('Login flow', () => {
  test('shows login form on /login', async ({ page }) => {
    await page.goto('/login')
    await expect(page.locator('h1')).toContainText('Connexion')
    await expect(page.locator('input[autocomplete="username"]')).toBeVisible()
    await expect(page.locator('button[type="submit"]')).toBeEnabled()
  })

  test('shows error on wrong credentials', async ({ page }) => {
    await page.goto('/login')
    await page.fill('input[autocomplete="username"]', 'wrong')
    await page.fill('input[autocomplete="current-password"]', 'wrongpassword123')
    await page.click('button[type="submit"]')
    await expect(page.locator('p.text-red-400')).toBeVisible({ timeout: 5_000 })
    await expect(page).toHaveURL(/\/login/)
  })

  test('redirects unauthenticated user to /login', async ({ page }) => {
    await page.goto('/login')
    await page.context().clearCookies()
    await page.goto('/')
    await expect(page).toHaveURL(/\/login/)
  })
})
