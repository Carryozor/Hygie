<!-- frontend/vue/src/views/LogsView.vue -->
<template>
  <div class="space-y-4">
    <div class="flex gap-3">
      <select v-model="level" class="bg-[var(--bg2)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm">
        <option value="">Tous niveaux</option>
        <option value="DEBUG">DEBUG</option>
        <option value="INFO">INFO</option>
        <option value="WARNING">WARNING</option>
        <option value="ERROR">ERROR</option>
      </select>
    </div>

    <div class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden font-mono text-xs">
      <div v-if="!logs.length" class="p-8 text-center text-[var(--muted)]">Aucun log.</div>
      <div
        v-for="log in logs"
        :key="log.id"
        class="flex gap-3 px-4 py-1.5 border-b border-[var(--border)] hover:bg-[var(--bg3)] transition-colors"
        :class="levelClass(log.level)"
      >
        <span class="text-[var(--muted)] shrink-0 w-40">{{ formatTs(log.ts) }}</span>
        <span class="font-semibold shrink-0 w-16">{{ log.level }}</span>
        <span class="truncate">{{ log.message }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import api from '@/api/client'

const logs  = ref([])
const level = ref('')

async function load() {
  const params = { limit: 200 }
  if (level.value) params.level = level.value
  const { data } = await api.get('/logs', { params })
  logs.value = data.logs || data || []
}

function levelClass(l) {
  return { ERROR: 'text-red-400', WARNING: 'text-yellow-400' }[l] || ''
}
function formatTs(ts) {
  return ts ? new Date(ts).toLocaleString('fr-FR') : ''
}

watch(level, load)
onMounted(load)
</script>
