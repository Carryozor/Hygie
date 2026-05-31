// frontend/vue/eslint.config.js
import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import prettier from 'eslint-config-prettier'

export default [
  js.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  prettier,
  {
    rules: {
      'vue/multi-word-component-names': 'off',   // Many existing single-word components
      'vue/require-default-prop': 'off',          // Optional in script setup
      'no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
      'no-console': 'warn',
    },
    ignores: ['dist/**', 'node_modules/**', 'e2e/**'],
  },
]
