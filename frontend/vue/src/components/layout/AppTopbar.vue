<template>
  <header class="h-14 flex items-center justify-between px-6 border-b border-[var(--border)] bg-[var(--bg2)] flex-shrink-0">
    <h1 class="font-semibold text-base">{{ pageTitle }}</h1>
    <div class="flex items-center gap-4">
      <span class="text-sm text-[var(--muted)]">{{ auth.username }}</span>
      <button @click="auth.logout()" class="text-[var(--muted)] hover:text-white text-sm transition-colors">
        <i class="fas fa-sign-out-alt" />
      </button>
    </div>
  </header>
</template>
<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const auth  = useAuthStore()

const titles = {
  dashboard: 'Dashboard',
  library:   'Bibliothèque',
  queue:     "File d'attente",
  rules:     'Règles',
  settings:  'Paramètres',
  logs:      'Journaux',
}
const pageTitle = computed(() => titles[route.name] || 'Hygie')
</script>
