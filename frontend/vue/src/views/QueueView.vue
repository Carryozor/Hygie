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

    <div class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden">
      <MediaTable :items="items" />
    </div>

    <!-- Pagination -->
    <div v-if="totalPages > 1" class="flex items-center justify-center gap-2">
      <button
        v-for="p in totalPages"
        :key="p"
        @click="page = p"
        class="w-8 h-8 rounded text-sm transition-colors"
        :class="p === page ? 'bg-[var(--accent)] text-white' : 'text-[var(--muted)] hover:bg-[var(--bg3)]'"
      >{{ p }}</button>
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
const perPage      = 50

const totalPages = computed(() => Math.ceil(total.value / perPage))

async function load() {
  const params = { limit: perPage, offset: (page.value - 1) * perPage }
  if (statusFilter.value) params.status = statusFilter.value
  if (search.value)        params.search = search.value
  const { data } = await api.get('/media', { params })
  items.value = data.items || data || []
  total.value = data.total || items.value.length
}

watch([statusFilter, search], () => { page.value = 1; load() })
watch(page, load)
onMounted(load)
</script>
