<template>
  <div class="space-y-6">
    <div v-if="error" class="bg-red-500/10 border border-red-500/30 text-red-400 rounded-lg px-4 py-3 text-sm">
      {{ error }}
    </div>
    <div class="grid grid-cols-2 xl:grid-cols-4 gap-4">
      <StatCard
        label="File d'attente"
        :value="stats.global.queue?.pending ?? 0"
        icon="fa-clock"
        :sub="`${stats.global.queue?.deleted ?? 0} supprimés`"
      />
      <StatCard
        label="Espace récupérable"
        :value="stats.storage.queue?.reclaimable_size ?? 0"
        icon="fa-hdd"
        format="bytes"
      />
      <StatCard
        label="Suppressions totales"
        :value="stats.global.total_deleted ?? 0"
        icon="fa-trash"
      />
      <StatCard
        label="Ce mois-ci"
        :value="currentMonthDeleted"
        icon="fa-calendar-check"
      />
    </div>

    <div class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden">
      <div class="px-5 py-4 border-b border-[var(--border)] flex items-center justify-between">
        <h2 class="font-semibold text-sm">File d'attente — tous les serveurs</h2>
        <router-link to="/queue" class="text-xs text-[var(--accent)] hover:underline">Voir tout →</router-link>
      </div>
      <div v-if="loading" class="p-8 text-center text-[var(--muted)] text-sm">Chargement...</div>
      <MediaTable v-else :items="recentQueue" :show-server-dot="true" />
    </div>
  </div>
</template>
<script setup>
import { computed, onMounted, ref } from 'vue'
import { useStatsStore } from '@/stores/stats'
import StatCard   from '@/components/ui/StatCard.vue'
import MediaTable from '@/components/media/MediaTable.vue'
import api from '@/api/client'

const stats   = useStatsStore()
const loading = ref(false)
const error   = ref('')
const recentQueue = ref([])

const currentMonthDeleted = computed(() => {
  const now = new Date()
  const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
  const entry = (stats.global.by_month || []).find(m => m.month === month)
  return entry?.total_deleted ?? 0
})

onMounted(async () => {
  loading.value = true
  error.value = ''
  try {
    await Promise.all([stats.fetchGlobal(), stats.fetchStorage()])
    const { data } = await api.get('/media', { params: { status: 'pending', limit: 10 } })
    recentQueue.value = data?.items || data || []
  } catch {
    error.value = 'Impossible de charger les données du tableau de bord.'
  } finally {
    loading.value = false
  }
})
</script>
