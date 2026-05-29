<template>
  <aside class="w-60 flex-shrink-0 flex flex-col border-r border-[var(--border)] bg-[var(--bg2)] h-screen sticky top-0 overflow-y-auto">
    <div class="flex items-center gap-3 px-4 py-5 border-b border-[var(--border)]">
      <HygieLogoSvg :size="32" />
      <span class="font-bold text-lg tracking-tight">Hygie</span>
    </div>

    <nav class="flex-1 px-2 py-4 space-y-1">
      <router-link
        v-for="item in navItems"
        :key="item.to"
        :to="item.to"
        class="flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-[var(--muted)] hover:text-white hover:bg-[var(--bg3)] transition-colors"
        active-class="bg-[var(--bg3)] text-white"
      >
        <i :class="['fas', item.icon, 'w-4 text-center']" />
        <span>{{ item.label }}</span>
      </router-link>

      <div class="pt-4 pb-1 px-3">
        <span class="text-xs font-semibold uppercase tracking-widest text-[var(--muted)]">Bibliothèques</span>
      </div>
      <ServerLibraryTree />
    </nav>

    <div class="px-3 py-4 border-t border-[var(--border)] space-y-2">
      <div
        v-if="isDryRun"
        class="flex items-center gap-2 px-3 py-2 rounded-lg bg-yellow-500/10 border border-yellow-500/30 text-yellow-400 text-xs"
      >
        <i class="fas fa-flask" />
        <span>Dry Run actif</span>
      </div>
    </div>
  </aside>
</template>
<script setup>
import { computed, onMounted } from 'vue'
import { useServersStore } from '@/stores/servers'
import { useSettingsStore } from '@/stores/settings'
import HygieLogoSvg from '@/components/ui/HygieLogoSvg.vue'
import ServerLibraryTree from './ServerLibraryTree.vue'

const servers  = useServersStore()
const settings = useSettingsStore()

const isDryRun = computed(() => settings.settings.dry_run === 'true' || settings.settings.dry_run === true)

const navItems = [
  { to: '/',       icon: 'fa-chart-bar', label: 'Dashboard' },
  { to: '/queue',  icon: 'fa-list',      label: "File d'attente" },
  { to: '/rules',  icon: 'fa-sliders-h', label: 'Règles' },
  { to: '/logs',   icon: 'fa-scroll',    label: 'Journaux' },
]

onMounted(async () => {
  await Promise.all([servers.fetch(), settings.fetch()])
})
</script>
