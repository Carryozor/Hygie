import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    outDir: '../dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks: {
          'vendor-vue':  ['vue', 'vue-router', 'pinia'],
          'vendor-i18n': ['vue-i18n'],
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/health': { target: 'http://localhost:8000', changeOrigin: true },
      '/static': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.js'],
    include: ['src/**/*.test.js'],
    exclude: ['e2e/**', 'node_modules/**'],
    coverage: {
      provider: 'v8',
      // Measure ALL of src/, not just files a test happens to import —
      // otherwise untested views/components silently don't count.
      include: ['src/**/*.{js,vue}'],
      exclude: ['src/**/__tests__/**', 'src/test-setup.js'],
      // Ratchet: measured 2026-09-29 at lines 96.6 / statements 94.7 /
      // branches 88.6 / functions 90.7. Raise, never lower.
      thresholds: { lines: 95, statements: 92, branches: 85, functions: 88 },
    },
  },
})
