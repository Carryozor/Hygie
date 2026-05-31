<template>
  <div class="space-y-6">
    <div v-if="error" class="bg-red-500/10 border border-red-500/30 text-red-400 rounded-lg px-4 py-3 text-sm">
      {{ error }}
    </div>

    <!-- Queue stats -->
    <div class="grid grid-cols-2 xl:grid-cols-4 gap-4">
      <StatCard
        label="En attente"
        :value="stats.global.queue?.pending ?? 0"
        icon="fa-clock"
        color="yellow"
        :sub="`${stats.global.queue?.total ?? 0} au total`"
      />
      <StatCard
        label="Suppressions totales"
        :value="stats.global.total_deleted ?? 0"
        icon="fa-check-circle"
        color="green"
        :sub="`${currentMonthDeleted} ce mois`"
      />
      <StatCard
        label="Ignorés"
        :value="stats.global.total_ignored ?? 0"
        icon="fa-ban"
        color="gray"
      />
      <StatCard
        label="Erreurs"
        :value="stats.global.queue?.error ?? 0"
        icon="fa-exclamation-circle"
        color="red"
      />
    </div>

    <!-- Storage row -->
    <div class="grid grid-cols-2 xl:grid-cols-4 gap-4">
      <StatCard
        label="Espace récupérable"
        :value="stats.storage.queue?.reclaimable_size ?? 0"
        icon="fa-hdd"
        color="blue"
        format="bytes"
      />
      <StatCard
        label="Films en bibliothèque"
        :value="stats.storage.movies?.total_in_library ?? stats.storage.movies?.count ?? 0"
        icon="fa-film"
        color="accent"
        :sub="stats.storage.movies?.size ? formatBytes(stats.storage.movies.size) : ''"
      />
      <StatCard
        label="Séries en bibliothèque"
        :value="stats.storage.series?.count ?? 0"
        icon="fa-tv"
        color="accent"
        :sub="stats.storage.series?.size ? formatBytes(stats.storage.series.size) : ''"
      />
      <StatCard
        label="Scans effectués"
        :value="stats.global.total_scans ?? 0"
        icon="fa-sync"
        color="accent"
      />
    </div>

    <!-- Radarr / Sonarr library details -->
    <div v-if="stats.storage.movies || stats.storage.series" class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <!-- Radarr -->
      <div v-if="stats.storage.movies" class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-5">
        <div class="flex items-center gap-2 mb-4">
          <i class="fas fa-film text-[var(--accent)] text-sm" />
          <h2 class="font-semibold text-sm">Films (Radarr)</h2>
          <span class="ml-auto text-xs font-medium text-[var(--muted)]">{{ formatBytes(stats.storage.movies.size) }}</span>
        </div>
        <div class="space-y-2">
          <div class="flex items-center justify-between text-sm">
            <span class="text-[var(--muted)]">En bibliothèque</span>
            <span class="font-semibold">{{ stats.storage.movies.total_in_library ?? stats.storage.movies.count ?? 0 }}</span>
          </div>
          <div class="flex items-center justify-between text-sm">
            <span class="text-[var(--muted)]">Sur disque</span>
            <span class="font-semibold">{{ stats.storage.movies.count ?? 0 }}</span>
          </div>
          <div v-if="(stats.storage.movies.total_in_library ?? 0) > (stats.storage.movies.count ?? 0)" class="flex items-center justify-between text-sm">
            <span class="text-[var(--muted)]">Manquants</span>
            <span class="font-semibold text-orange-400">{{ (stats.storage.movies.total_in_library ?? 0) - (stats.storage.movies.count ?? 0) }}</span>
          </div>
          <div class="border-t border-[var(--border)] pt-2 mt-2 flex items-center justify-between text-sm">
            <span class="text-[var(--muted)]">Monitored</span>
            <span class="font-semibold text-green-400">{{ stats.storage.movies.monitored ?? 0 }}</span>
          </div>
          <div class="flex items-center justify-between text-sm">
            <span class="text-[var(--muted)]">Non monitored</span>
            <span class="font-semibold text-[var(--muted)]">{{ stats.storage.movies.unmonitored ?? 0 }}</span>
          </div>
        </div>
      </div>
      <!-- Sonarr -->
      <div v-if="stats.storage.series" class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-5">
        <div class="flex items-center gap-2 mb-4">
          <i class="fas fa-tv text-[var(--accent)] text-sm" />
          <h2 class="font-semibold text-sm">Séries (Sonarr)</h2>
          <span class="ml-auto text-xs font-medium text-[var(--muted)]">{{ formatBytes(stats.storage.series.size) }}</span>
        </div>
        <div class="space-y-2">
          <div class="flex items-center justify-between text-sm">
            <span class="text-[var(--muted)]">Séries</span>
            <span class="font-semibold">{{ stats.storage.series.count ?? 0 }}</span>
          </div>
          <div class="flex items-center justify-between text-sm">
            <span class="text-[var(--muted)]">Épisodes sur disque</span>
            <span class="font-semibold">{{ (stats.storage.series.episodes ?? 0).toLocaleString('fr-FR') }}</span>
          </div>
          <div v-if="stats.storage.series.episodes_total" class="flex items-center justify-between text-sm">
            <span class="text-[var(--muted)]">Total épisodes</span>
            <span class="font-semibold">{{ stats.storage.series.episodes_total.toLocaleString('fr-FR') }}</span>
          </div>
          <div v-if="stats.storage.series.episodes_aired" class="flex items-center justify-between text-sm">
            <span class="text-[var(--muted)]">Épisodes diffusés</span>
            <span class="font-semibold">{{ stats.storage.series.episodes_aired.toLocaleString('fr-FR') }}</span>
          </div>
          <div class="border-t border-[var(--border)] pt-2 mt-2 flex items-center justify-between text-sm">
            <span class="text-[var(--muted)]">Monitored</span>
            <span class="font-semibold text-green-400">{{ stats.storage.series.monitored ?? 0 }}</span>
          </div>
          <div class="flex items-center justify-between text-sm">
            <span class="text-[var(--muted)]">Non monitored</span>
            <span class="font-semibold text-[var(--muted)]">{{ stats.storage.series.unmonitored ?? 0 }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Top 5 upcoming deletions -->
    <div class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden">
      <div class="px-5 py-4 border-b border-[var(--border)] flex items-center justify-between">
        <h2 class="font-semibold text-sm">5 prochaines suppressions</h2>
        <router-link to="/queue" class="text-xs text-[var(--accent)] hover:underline flex items-center gap-1">
          <i class="fas fa-list text-[10px]" /> Voir tout
        </router-link>
      </div>
      <div v-if="loading" class="p-8 text-center text-[var(--muted)] text-sm">Chargement...</div>
      <div v-else-if="!recentQueue.length" class="p-8 text-center text-[var(--muted)] text-sm">
        <i class="fas fa-check-circle text-2xl mb-2 block opacity-30" />
        Aucune suppression planifiée
      </div>
      <table v-else class="w-full text-sm">
        <tbody>
          <tr
v-for="item in recentQueue" :key="item.id"
            class="border-b border-[var(--border)] last:border-b-0 hover:bg-[var(--bg3)] transition-colors">
            <!-- Poster -->
            <td class="px-3 py-1.5 w-12">
              <div class="relative w-8 h-12 rounded overflow-hidden bg-[var(--bg3)] flex items-center justify-center">
                <img
v-if="item.poster_url"
                  :src="`/api/proxy/image?url=${encodeURIComponent(item.poster_url)}`"
                  :alt="item.title"
                  class="w-full h-full object-cover absolute inset-0"
                  loading="lazy"
                  @error="e => e.target.style.display = 'none'" />
                <i :class="['fas', item.media_type === 'Episode' || item.media_type === 'Series' ? 'fa-tv' : 'fa-film', 'text-xs text-[var(--muted)] opacity-40']" />
              </div>
            </td>
            <!-- Title + meta -->
            <td class="px-3 py-2 max-w-[200px]">
              <a
v-if="item.seerr_request_url" :href="item.seerr_request_url" target="_blank"
                class="font-medium text-sm truncate block hover:text-[var(--accent)] transition-colors" :title="item.title">
                {{ item.title }}
              </a>
              <span v-else class="font-medium text-sm truncate block" :title="item.title">{{ item.title }}</span>
              <div class="text-xs text-[var(--muted)] truncate">{{ item.library_name }}</div>
              <div v-if="item.seerr_username" class="text-xs text-[var(--muted)] truncate">
                <i class="fas fa-user text-[10px] opacity-50 mr-0.5" />{{ item.seerr_username }}
              </div>
            </td>
            <!-- Status + detected -->
            <td class="px-3 py-2 hidden sm:table-cell whitespace-nowrap">
              <span
class="px-1.5 py-0.5 rounded text-[10px]"
                :class="item.status === 'deleted' ? 'bg-green-500/20 text-green-400' : item.status === 'error' ? 'bg-red-700/20 text-red-300' : 'bg-yellow-500/20 text-yellow-400'">
                {{ item.status === 'deleted' ? 'Supprimé' : item.status === 'error' ? 'Erreur' : 'En attente' }}
              </span>
              <div v-if="item.detected_at" class="text-[10px] text-[var(--muted)] mt-0.5">
                Ajouté {{ formatDate(item.detected_at) }}
              </div>
            </td>
            <!-- Days remaining -->
            <td class="px-3 py-2 text-right whitespace-nowrap">
              <span :class="daysClass(item.delete_at)" class="text-xs font-semibold">
                {{ daysLabel(item.delete_at) }}
              </span>
              <div class="text-xs text-[var(--muted)]">{{ formatDate(item.delete_at) }}</div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Disk usage -->
    <div v-if="stats.storage.disks?.length" class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-5 space-y-3">
      <h2 class="font-semibold text-sm">Espace disque</h2>
      <div v-for="disk in stats.storage.disks" :key="disk.path" class="space-y-1">
        <div class="flex justify-between text-xs text-[var(--muted)]">
          <span>{{ disk.path }} <span class="text-[var(--border)]">({{ disk.source }})</span></span>
          <span>{{ formatBytes(disk.total - disk.free) }} / {{ formatBytes(disk.total) }}</span>
        </div>
        <div class="h-1.5 bg-[var(--bg3)] rounded-full overflow-hidden">
          <div
            class="h-full rounded-full transition-all"
            :class="diskBarClass(disk)"
            :style="{ width: diskPercent(disk) + '%' }"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useStatsStore } from '@/stores/stats'
import StatCard from '@/components/ui/StatCard.vue'
import api from '@/api/client'

const stats   = useStatsStore()
const loading = ref(false)
const error   = ref('')
const recentQueue = ref([])

const currentMonthDeleted = computed(() => {
  const now   = new Date()
  const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
  const entry = (stats.global.by_month || []).find(m => m.month === month)
  return entry?.deleted ?? 0
})

function formatBytes(b) {
  if (!b) return ''
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(0)} KB`
  if (b < 1024 ** 3) return `${(b / 1024 ** 2).toFixed(1)} MB`
  return `${(b / 1024 ** 3).toFixed(1)} GB`
}

function diskPercent(disk) {
  if (!disk.total) return 0
  return Math.round(((disk.total - disk.free) / disk.total) * 100)
}
function diskBarClass(disk) {
  const p = diskPercent(disk)
  if (p > 90) return 'bg-red-400'
  if (p > 75) return 'bg-orange-400'
  return 'bg-[var(--accent)]'
}

function daysRemaining(deleteAt) {
  if (!deleteAt) return null
  return Math.ceil((new Date(deleteAt) - new Date()) / 86400000)
}
function daysLabel(deleteAt) {
  const d = daysRemaining(deleteAt)
  if (d === null) return '—'
  if (d < 0)  return 'Dépassé'
  if (d === 0) return "Aujourd'hui"
  if (d === 1) return 'Demain'
  return `Dans ${d}j`
}
function daysClass(deleteAt) {
  const d = daysRemaining(deleteAt)
  if (d === null) return 'text-[var(--muted)]'
  if (d <= 3)  return 'text-red-400'
  if (d <= 7)  return 'text-orange-400'
  if (d <= 14) return 'text-yellow-400'
  return 'text-[var(--muted)]'
}
function formatDate(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric' })
}

onMounted(async () => {
  loading.value = true
  error.value   = ''
  try {
    await Promise.all([stats.fetchGlobal(), stats.fetchStorage()])
    const { data } = await api.get('/media', { params: { status: 'pending', limit: 5, sort: 'delete_at', dir: 'asc' } })
    recentQueue.value = data?.items || data || []
  } catch {
    error.value = 'Impossible de charger les données du tableau de bord.'
  } finally {
    loading.value = false
  }
})
</script>
