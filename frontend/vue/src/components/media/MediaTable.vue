<template>
  <div>
    <div v-if="!items.length" class="py-8 text-center text-[var(--muted)] text-sm">
      Aucun élément.
    </div>
    <div v-else class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="text-xs text-[var(--muted)] border-b border-[var(--border)]">
            <th class="w-14 px-3 py-2" />
            <th class="text-left px-4 py-2">Titre</th>
            <th class="text-left px-4 py-2 hidden md:table-cell">Bibliothèque</th>
            <th class="text-left px-4 py-2 hidden lg:table-cell">Demandeur</th>
            <th class="text-left px-4 py-2">Statut</th>
            <th class="text-left px-4 py-2">Suppression</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in items"
            :key="item.id"
            class="border-b border-[var(--border)] hover:bg-[var(--bg3)] transition-colors"
            :class="rowUrgencyClass(item.delete_at, item.status)"
          >
            <!-- Thumbnail -->
            <td class="px-3 py-1.5 w-14">
              <div class="relative w-10 h-14 rounded overflow-hidden bg-[var(--bg3)] flex-shrink-0 flex items-center justify-center">
                <img
                  v-if="item.poster_url"
                  :src="`/api/proxy/image?url=${encodeURIComponent(item.poster_url)}`"
                  :alt="item.title"
                  class="w-full h-full object-cover absolute inset-0"
                  loading="lazy"
                  @error="e => e.target.style.display = 'none'"
                />
                <i :class="['fas', isSeriesType(item.media_type) ? 'fa-tv' : 'fa-film', 'text-sm text-[var(--muted)] opacity-50']" />
              </div>
            </td>

            <!-- Title + server dot -->
            <td class="px-4 py-2">
              <div class="flex items-center gap-2">
                <span
                  v-if="showServerDot"
                  class="w-2 h-2 rounded-full flex-shrink-0"
                  :class="serverDotClass(item.server_id)"
                />
                <span class="font-medium truncate max-w-[160px] xl:max-w-[260px]" :title="item.title">
                  {{ item.title }}
                </span>
              </div>
            </td>

            <!-- Library -->
            <td class="px-4 py-2 text-[var(--muted)] hidden md:table-cell truncate max-w-[130px]">
              {{ item.library_name || '—' }}
            </td>

            <!-- Requester -->
            <td class="px-4 py-2 text-[var(--muted)] hidden lg:table-cell">
              {{ item.seerr_username || '—' }}
            </td>

            <!-- Status badge -->
            <td class="px-4 py-2">
              <span class="px-2 py-0.5 rounded text-xs whitespace-nowrap" :class="statusClass(item.status)">
                {{ statusLabel(item.status) }}
              </span>
            </td>

            <!-- Days remaining + date -->
            <td class="px-4 py-2 whitespace-nowrap">
              <span :class="daysClass(item.delete_at, item.status)" class="text-xs font-semibold block">
                {{ daysLabel(item.delete_at, item.status) }}
              </span>
              <span class="text-xs text-[var(--muted)]">{{ formatDate(item.delete_at) }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { useServersStore } from '@/stores/servers'

defineProps({
  items:         { type: Array, default: () => [] },
  showServerDot: { type: Boolean, default: false },
})

const servers = useServersStore()

const STATUS_LABELS = {
  pending: 'En attente',
  deleted: 'Supprimé',
  error:   'Erreur',
}
const STATUS_CLASSES = {
  pending: 'bg-yellow-500/20 text-yellow-400',
  deleted: 'bg-red-500/20 text-red-400',
  error:   'bg-red-700/20 text-red-300',
}
const SERVER_DOT_CLASSES = {
  plex:     'bg-orange-400',
  jellyfin: 'bg-green-400',
  emby:     'bg-blue-400',
}

function isSeriesType(type) {
  return type === 'Episode' || type === 'Series' || type === 'Season'
}

function statusLabel(s) {
  return STATUS_LABELS[s] || s
}
function statusClass(s) {
  return STATUS_CLASSES[s] || 'bg-[var(--bg3)] text-[var(--muted)]'
}

function serverDotClass(serverId) {
  const srv = servers.servers.find(s => String(s.id) === String(serverId))
  return SERVER_DOT_CLASSES[srv?.type] || 'bg-[var(--muted)]'
}

function daysRemaining(deleteAt) {
  if (!deleteAt) return null
  const diff = new Date(deleteAt) - new Date()
  return Math.ceil(diff / (1000 * 60 * 60 * 24))
}

function daysLabel(deleteAt, status) {
  if (status === 'deleted') return 'Supprimé'
  if (!deleteAt) return '—'
  const d = daysRemaining(deleteAt)
  if (d === null) return '—'
  if (d < 0)  return 'Dépassé'
  if (d === 0) return "Aujourd'hui"
  if (d === 1) return 'Demain'
  return `Dans ${d}j`
}

function daysClass(deleteAt, status) {
  if (status === 'deleted') return 'text-[var(--muted)]'
  const d = daysRemaining(deleteAt)
  if (d === null) return 'text-[var(--muted)]'
  if (d <= 3)  return 'text-red-400'
  if (d <= 7)  return 'text-orange-400'
  if (d <= 14) return 'text-yellow-400'
  return 'text-[var(--muted)]'
}

function rowUrgencyClass(deleteAt, status) {
  if (status !== 'pending') return ''
  const d = daysRemaining(deleteAt)
  if (d === null) return ''
  if (d <= 3) return 'bg-red-500/5'
  if (d <= 7) return 'bg-orange-500/5'
  return ''
}

function formatDate(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric' })
}
</script>
