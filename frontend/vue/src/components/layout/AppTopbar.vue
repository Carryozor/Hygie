<template>
  <header class="h-14 flex items-center justify-between px-6 border-b border-[var(--border)] bg-[var(--bg2)] flex-shrink-0">
    <h1 class="font-semibold text-base">{{ pageTitle }}</h1>
    <div class="flex items-center gap-3">
      <!-- Username + logout -->
      <span class="text-sm text-[var(--muted)] hidden sm:inline">{{ auth.username }}</span>
      <button
        class="text-[var(--muted)] hover:text-white text-sm transition-colors"
        :title="t('auth.logout')"
        @click="handleLogout"
      >
        <i class="fas fa-right-from-bracket" />
      </button>

      <div class="w-px h-4 bg-[var(--border)]" />

      <!-- Language selector -->
      <select
        v-model="lang"
        class="bg-[var(--bg3)] border border-[var(--border)] rounded-md px-2 py-1 text-xs text-[var(--muted)] focus:outline-none focus:border-[var(--accent)] cursor-pointer"
        title="Langue / Language"
        @change="saveLang"
      >
        <option value="fr">🇫🇷 FR</option>
        <option value="en">🇬🇧 EN</option>
        <option value="de">🇩🇪 DE</option>
        <option value="es">🇪🇸 ES</option>
        <option value="it">🇮🇹 IT</option>
        <option value="pt">🇵🇹 PT</option>
        <option value="nl">🇳🇱 NL</option>
        <option value="pl">🇵🇱 PL</option>
      </select>

      <!-- Soutenir Hygie (beating heart) -->
      <a
        href="https://github.com/sponsors/carryozor"
        target="_blank"
        rel="noopener noreferrer"
        title="Soutenir Hygie"
        class="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md bg-pink-500/10 border border-pink-500/30 text-pink-400 hover:bg-pink-500/20 transition-colors"
      >
        <i class="fas fa-heart text-[10px] heart-beat" />
        <span class="hidden sm:inline">{{ t('auth.support') }}</span>
      </a>
    </div>
  </header>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '@/stores/auth'
import { setLocale } from '@/i18n'
import api from '@/api/client'

const { t } = useI18n()
const route  = useRoute()
const router = useRouter()
const auth   = useAuthStore()
const lang   = ref(localStorage.getItem('hygie_lang') || 'fr')

const PAGE_ROUTE_KEYS = {
  dashboard: 'nav.dashboard',
  library:   'nav.libraries',
  queue:     'nav.queue',
  calendar:  'nav.calendar',
  rules:     'nav.rules',
  settings:  'settings.title',
  logs:      'nav.logs',
  ignored:   'nav.ignored',
}
const pageTitle = computed(() => {
  const key = PAGE_ROUTE_KEYS[route.name]
  return key ? t(key) : 'Hygie'
})

async function saveLang() {
  setLocale(lang.value)
  try { await api.post('/settings', { ui_language: lang.value }) } catch { /* silent */ }
}

onMounted(async () => {
  try {
    const { data } = await api.get('/settings')
    const savedLang = data.ui_language || 'fr'
    lang.value = savedLang
    setLocale(savedLang)
  } catch { /* silent */ }
})

function handleLogout() {
  auth.logout()
  router.push('/login')
}
</script>
