<!-- frontend/vue/src/views/LibraryView.vue -->
<template>
  <div v-if="loading" class="text-[var(--muted)] text-sm p-8">Chargement...</div>
  <div v-else-if="error" class="bg-red-500/10 border border-red-500/30 text-red-400 rounded-lg px-4 py-3 text-sm">
    {{ error }}
  </div>
  <div v-else-if="library" class="space-y-6">
    <div class="flex items-center gap-4">
      <h2 class="font-bold text-xl">{{ library.name }}</h2>
      <span class="text-xs text-[var(--muted)] bg-[var(--bg3)] px-2 py-1 rounded">{{ library.deletion_unit }}</span>
    </div>
    <div class="grid grid-cols-2 xl:grid-cols-4 gap-4">
      <StatCard label="En attente" :value="queueCount" icon="fa-clock" />
      <StatCard label="Supprimés" :value="deletedCount" icon="fa-trash" />
    </div>
    <div class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden">
      <MediaTable :items="items" />
    </div>
  </div>
  <div v-else class="text-[var(--muted)] text-sm p-8">Bibliothèque introuvable.</div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useServersStore } from '@/stores/servers'
import api from '@/api/client'
import StatCard   from '@/components/ui/StatCard.vue'
import MediaTable from '@/components/media/MediaTable.vue'

const route   = useRoute()
const servers = useServersStore()
const items   = ref([])
const loading = ref(false)
const error   = ref('')

const library = computed(() => servers.libraries.find(l => String(l.id) === String(route.params.id)))
const queueCount   = computed(() => items.value.filter(i => i.status === 'pending').length)
const deletedCount = computed(() => items.value.filter(i => i.status === 'deleted').length)

onMounted(async () => {
  loading.value = true
  error.value = ''
  try {
    if (!servers.libraries.length) await servers.fetch()
    const { data } = await api.get('/media', { params: { library_id: route.params.id, limit: 200 } })
    items.value = data.items || data || []
  } catch {
    error.value = 'Impossible de charger la bibliothèque.'
  } finally {
    loading.value = false
  }
})
</script>
