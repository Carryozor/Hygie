<template>
  <div class="space-y-4">
    <div v-for="server in servers.servers" :key="server.id">
      <div class="flex items-center gap-2 px-3 py-1">
        <span class="w-2 h-2 rounded-full flex-shrink-0" :class="serverDotClass(server.type)" />
        <span class="text-xs font-semibold uppercase tracking-widest text-[var(--muted)] truncate">
          {{ server.name || 'Serveur' }}
        </span>
      </div>
      <router-link
        v-for="lib in servers.librariesForServer(server.id)"
        :key="lib.id"
        :to="{ name: 'library', params: { id: lib.id } }"
        class="flex items-center gap-2 px-4 py-1.5 rounded-lg text-sm transition-colors hover:bg-[var(--bg3)] text-[var(--muted)] hover:text-white"
        active-class="bg-[var(--bg3)] text-white"
      >
        <i class="fas fa-film text-xs opacity-60" />
        <span class="truncate">{{ lib.name }}</span>
      </router-link>
    </div>
  </div>
</template>
<script setup>
import { useServersStore } from '@/stores/servers'
const servers = useServersStore()
function serverDotClass(type) {
  return {
    'bg-orange-400':        type === 'plex',
    'bg-green-400':         type === 'jellyfin',
    'bg-blue-400':          type === 'emby',
    'bg-[var(--muted)]':    !type,
  }
}
</script>
