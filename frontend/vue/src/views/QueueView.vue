<!-- frontend/vue/src/views/QueueView.vue -->
<template>
  <div class="space-y-4">
    <div class="flex items-center gap-4 flex-wrap">
      <select v-model="statusFilter" class="bg-[var(--bg2)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm">
        <option value="">Tous les statuts</option>
        <option value="pending">En attente</option>
        <option value="deleted">Supprimés</option>
        <option value="error">Erreur</option>
      </select>
      <input
        v-model="search"
        placeholder="Rechercher..."
        class="bg-[var(--bg2)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm w-64 focus:outline-none focus:border-[var(--accent)]"
      />
      <span class="text-sm text-[var(--muted)] ml-auto">{{ total }} éléments</span>
    </div>

    <div v-if="error" class="bg-red-500/10 border border-red-500/30 text-red-400 rounded-lg px-4 py-3 text-sm">
      {{ error }}
    </div>

    <div class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden">
      <div v-if="loading" class="p-8 text-center text-[var(--muted)] text-sm">Chargement...</div>
      <MediaTable v-else :items="items" />
    </div>

    <!-- Windowed pagination -->
    <div v-if="totalPages > 1" class="flex items-center justify-center gap-1">
      <button
        @click="page = 1"
        :disabled="page === 1"
        class="w-8 h-8 rounded text-sm text-[var(--muted)] hover:bg-[var(--bg3)] disabled:opacity-30"
      >«</button>
      <button
        v-for="p in visiblePages"
        :key="p"
        @click="typeof p === 'number' && (page = p)"
        class="w-8 h-8 rounded text-sm transition-colors"
        :class="p === page ? 'bg-[var(--accent)] text-white' : p === '…' ? 'cursor-default text-[var(--muted)]' : 'text-[var(--muted)] hover:bg-[var(--bg3)]'"
      >{{ p }}</button>
      <button
        @click="page = totalPages"
        :disabled="page === totalPages"
        class="w-8 h-8 rounded text-sm text-[var(--muted)] hover:bg-[var(--bg3)] disabled:opacity-30"
      >»</button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import api from '@/api/client'
import MediaTable from '@/components/media/MediaTable.vue'

const items        = ref([])
const total        = ref(0)
const page         = ref(1)
const statusFilter = ref('')
const search       = ref('')
const loading      = ref(false)
const error        = ref('')
const perPage      = 50

const totalPages = computed(() => Math.ceil(total.value / perPage))

const visiblePages = computed(() => {
  const n = totalPages.value
  const p = page.value
  if (n <= 7) return Array.from({ length: n }, (_, i) => i + 1)
  const start = Math.max(1, p - 2)
  const end   = Math.min(n, p + 2)
  const pages = []
  if (start > 2)      pages.push(1, '…')
  else if (start === 2) pages.push(1)
  for (let i = start; i <= end; i++) pages.push(i)
  if (end < n - 1)    pages.push('…', n)
  else if (end === n - 1) pages.push(n)
  return pages
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = { limit: perPage, offset: (page.value - 1) * perPage }
    if (statusFilter.value) params.status = statusFilter.value
    if (search.value)        params.search = search.value
    const { data } = await api.get('/media', { params })
    items.value = data.items || data || []
    total.value = data.total || items.value.length
  } catch {
    error.value = 'Impossible de charger la file d\'attente.'
  } finally {
    loading.value = false
  }
}

watch([statusFilter, search], () => { page.value = 1; load() })
watch(page, load)
onMounted(load)
</script>
