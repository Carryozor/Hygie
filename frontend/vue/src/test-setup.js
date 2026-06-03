// frontend/vue/src/test-setup.js
// Global setup for Vitest tests
// Provides a fresh Pinia instance for each test file
import { config } from '@vue/test-utils'
import { createPinia } from 'pinia'

config.global.plugins = [createPinia()]
