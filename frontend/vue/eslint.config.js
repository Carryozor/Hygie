// frontend/vue/eslint.config.js
import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import prettier from 'eslint-config-prettier'
import globals from 'globals'

export default [
  js.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  prettier,
  {
    languageOptions: {
      // Use globals.browser instead of a manual list — covers all standard browser
      // APIs automatically (alert, sessionStorage, fetch, URL, etc.) so we never
      // break CI by using a new browser API that wasn't manually whitelisted.
      globals: {
        ...globals.browser,
        process: 'readonly', // injected by Vite for import.meta.env compatibility
      },
    },
    rules: {
      'vue/multi-word-component-names': 'off',
      'vue/require-default-prop': 'off',
      // Settings tabs share a form object by reference (intentional architectural choice).
      // Components receive the settings form as a prop and mutate fields in-place;
      // this avoids prop-drilling a separate emit for every field change.
      'vue/no-mutating-props': 'warn',
      'no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      'no-console': 'warn',
    },
    ignores: ['dist/**', 'node_modules/**', 'e2e/**'],
  },
]
