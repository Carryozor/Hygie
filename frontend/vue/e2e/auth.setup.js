// frontend/vue/e2e/auth.setup.js
import { test as setup, expect } from '@playwright/test'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const AUTH_FILE = path.join(__dirname, '.auth/state.json')

setup('authenticate', async ({ page }) => {
  const username = process.env.E2E_USERNAME || 'admin'
  const password = process.env.E2E_PASSWORD || 'changeme'

  await page.goto('/login')
  await page.waitForSelector('input[autocomplete="username"]', { timeout: 10_000 })

  await page.fill('input[autocomplete="username"]', username)
  await page.fill('input[autocomplete="current-password"]', password)
  await page.click('button[type="submit"]')

  await page.waitForURL('/', { timeout: 15_000 })
  await page.context().storageState({ path: AUTH_FILE })
})
