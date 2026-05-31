<template>
  <header class="h-14 flex items-center justify-between px-6 border-b border-[var(--border)] bg-[var(--bg2)] flex-shrink-0">
    <h1 class="font-semibold text-base">{{ pageTitle }}</h1>
    <div class="flex items-center gap-3">
      <!-- Username + logout -->
      <span class="text-sm text-[var(--muted)] hidden sm:inline">{{ auth.username }}</span>
      <button
        class="text-[var(--muted)] hover:text-white text-sm transition-colors"
        title="Déconnexion"
        @click="handleLogout"
      >
        <i class="fas fa-sign-out-alt" />
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
        <span class="hidden sm:inline">Soutenir</span>
      </a>
    </div>
  </header>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import api from '@/api/client'

const route  = useRoute()
const router = useRouter()
const auth   = useAuthStore()

const lang = ref('fr')

const titles = {
  dashboard: 'Dashboard',
  library:   'Bibliothèque',
  queue:     "File d'attente",
  calendar:  'Calendrier',
  rules:     'Règles',
  settings:  'Paramètres',
  logs:      'Journaux',
  ignored:   'Ignorés',
}
const pageTitle = computed(() => titles[route.name] || 'Hygie')

async function saveLang() {
  try { await api.post('/settings', { ui_language: lang.value }) } catch { /* silent */ }
}

onMounted(async () => {
  try {
    const { data } = await api.get('/settings')
    lang.value = data.ui_language || 'fr'
  } catch { /* silent */ }
})

function handleLogout() {
  auth.logout()
  router.push('/login')
}
</script>
