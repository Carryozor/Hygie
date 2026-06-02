<template>
  <div class="space-y-1">
    <div v-for="server in servers.servers" :key="server.id">
      <div
        class="flex items-center gap-2 px-3 py-1 cursor-pointer select-none rounded hover:bg-[var(--bg3)] transition-colors"
        @click="toggleServer(server.id)"
      >
        <span class="w-2 h-2 rounded-full flex-shrink-0" :class="serverDotClass(server.type)" />
        <span class="text-xs font-semibold uppercase tracking-widest text-[var(--muted)] truncate flex-1">
          {{ server.name || 'Serveur' }}
        </span>
        <i
          class="fas fa-chevron-down text-[var(--muted)] text-xs transition-transform duration-200"
          :style="collapsed[server.id] ? 'transform:rotate(-90deg)' : ''"
        />
      </div>
      <div v-show="!collapsed[server.id]">
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
  </div>
</template>
<script setup>
import { reactive } from 'vue'
import { useServersStore } from '@/stores/servers'

const servers = useServersStore()
const collapsed = reactive({})

function toggleServer(id) {
  collapsed[id] = !collapsed[id]
}

function serverDotClass(type) {
  return {
    'bg-orange-400':     type === 'plex',
    'bg-violet-500':     type === 'jellyfin',
    'bg-green-500':      type === 'emby',
    'bg-[var(--muted)]': !type,
  }
}
</script>
