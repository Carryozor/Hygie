<!-- frontend/vue/src/views/LogsView.vue -->
<template>
  <div class="space-y-4">
    <!-- Toolbar -->
    <div class="flex items-center gap-3 flex-wrap">
      <div class="flex bg-[var(--bg2)] border border-[var(--border)] rounded-lg p-0.5">
        <button
          v-for="f in LEVELS"
          :key="f.value"
          class="px-3 py-1.5 text-xs rounded transition-colors"
          :class="level === f.value ? 'bg-[var(--accent)] text-white' : 'text-[var(--muted)] hover:text-white'"
          @click="level = f.value"
        >{{ f.label }}</button>
      </div>

      <button
        class="w-8 h-8 rounded bg-[var(--bg2)] border border-[var(--border)] flex items-center justify-center text-[var(--muted)] hover:text-white transition-colors"
        title="Rafraîchir"
        @click="load"
      ><i class="fas fa-sync text-xs" :class="loading ? 'fa-spin' : ''" /></button>

      <!-- Mass mark as seen (green) -->
      <button
        v-if="hasUnseenErrors"
        :disabled="bulkWorking"
        class="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-green-500/40 text-green-400 hover:bg-green-500/10 transition-colors disabled:opacity-50"
        title="Marquer toutes les erreurs comme vues"
        @click="markSeenAll"
      >
        <i class="fas fa-check text-[10px]" />
        Tout marquer comme vu
      </button>

      <!-- Mass acknowledge (orange question mark) -->
      <button
        v-if="hasUnseenErrors"
        :disabled="bulkWorking"
        class="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-orange-500/40 text-orange-400 hover:bg-orange-500/10 transition-colors disabled:opacity-50"
        title="Accuser réception de toutes les erreurs"
        @click="ackAll"
      >
        <i class="fas fa-question text-[10px]" />
        Accuser réception
      </button>

      <span class="text-xs text-[var(--muted)] ml-auto">{{ logs.length }} entrée(s)</span>
    </div>

    <div v-if="fetchError" class="bg-red-500/10 border border-red-500/30 text-red-400 rounded-lg px-4 py-3 text-sm">{{ fetchError }}</div>

    <div class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden font-mono text-xs">
      <div v-if="loading" class="p-8 text-center text-[var(--muted)]">Chargement...</div>
      <div v-else-if="!logs.length" class="p-8 text-center text-[var(--muted)]">Aucun log.</div>
      <template v-else>
        <div
          v-for="log in logs"
          :key="log.id"
          class="flex gap-3 px-4 py-1.5 border-b border-[var(--border)] last:border-b-0 transition-colors group"
          :class="rowClass(log)"
        >
          <span class="shrink-0 w-36 text-[var(--muted)]">{{ formatTs(log.ts) }}</span>
          <span class="shrink-0 w-16 font-bold uppercase" :class="levelClass(log.level)">{{ log.level }}</span>
          <span class="break-all flex-1" :class="strikeClass(log)">{{ log.message }}</span>

          <!-- Right side -->
          <div class="shrink-0 flex items-center gap-1 ml-1">
            <!-- Status badges (always visible when set) -->
            <span v-if="log.seen_status === 'seen'"  class="text-green-400/80"><i class="fas fa-check text-[10px]" /></span>
            <span v-if="log.seen_status === 'acked'" class="text-yellow-400/80"><i class="fas fa-question text-[10px]" /></span>

            <!-- Mark as seen (hover, only when not already seen) -->
            <button
              v-if="log.seen_status !== 'seen'"
              class="opacity-0 group-hover:opacity-100 transition-opacity w-5 h-5 rounded flex items-center justify-center text-green-400/70 hover:text-green-400 hover:bg-green-500/10"
              title="Marquer comme vu"
              @click.stop="markSeen(log)"
            ><i class="fas fa-check text-[10px]" /></button>

            <!-- Clear status (hover, only when marked) -->
            <button
              v-if="log.seen_status"
              class="opacity-0 group-hover:opacity-100 transition-opacity w-5 h-5 rounded flex items-center justify-center text-[var(--muted)] hover:text-white hover:bg-[var(--bg3)]"
              title="Réinitialiser"
              @click.stop="clearStatus(log)"
            ><i class="fas fa-times text-[10px]" /></button>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import api from '@/api/client'

const LEVELS = [
  { value: '',        label: 'Tous' },
  { value: 'DEBUG',   label: 'Debug' },
  { value: 'INFO',    label: 'Info' },
  { value: 'WARNING', label: 'Warning' },
  { value: 'ERROR',   label: 'Erreur' },
]

const logs        = ref([])
const level       = ref('')
const loading     = ref(false)
const bulkWorking = ref(false)
const fetchError  = ref('')

const hasUnseenErrors = computed(() =>
  logs.value.some(l => l.level === 'ERROR' && !l.seen_status)
)

// Row background — keep red for errors regardless of seen status
function rowClass(log) {
  if (log.level === 'ERROR')   return 'bg-red-500/5 hover:bg-red-500/10'
  if (log.level === 'WARNING') return 'bg-yellow-500/5 hover:bg-yellow-500/10'
  return 'hover:bg-[var(--bg3)]'
}

// Level badge color — unchanged by seen status
function levelClass(lvl) {
  const map = { DEBUG: 'text-green-400', INFO: 'text-blue-400', WARNING: 'text-yellow-400', ERROR: 'text-red-400' }
  return map[lvl] || 'text-[var(--muted)]'
}

// Strikethrough on message text based on seen status
function strikeClass(log) {
  if (log.seen_status === 'seen')  return 'line-through decoration-green-400/70  text-green-400/60'
  if (log.seen_status === 'acked') return 'line-through decoration-orange-400/70 text-orange-400/60'
  return ''
}

function formatTs(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

async function load() {
  loading.value = true; fetchError.value = ''
  try {
    const params = { limit: 300 }
    if (level.value) params.level = level.value
    const { data } = await api.get('/logs', { params })
    logs.value = data.logs || data || []
  } catch { fetchError.value = 'Impossible de charger les journaux.' }
  finally { loading.value = false }
}

async function markSeen(log) {
  try {
    await api.patch(`/logs/${log.id}`, { seen_status: 'seen' })
    log.seen_status = 'seen'
  } catch { /* silent */ }
}

async function clearStatus(log) {
  try {
    await api.patch(`/logs/${log.id}`, { seen_status: null })
    log.seen_status = null
  } catch { /* silent */ }
}

async function markSeenAll() {
  bulkWorking.value = true
  try {
    await api.post('/logs/mark-seen-errors')
    logs.value.forEach(l => { if (l.level === 'ERROR' && !l.seen_status) l.seen_status = 'seen' })
  } catch { /* silent */ } finally { bulkWorking.value = false }
}

async function ackAll() {
  bulkWorking.value = true
  try {
    await api.post('/logs/ack-errors')
    logs.value.forEach(l => { if (l.level === 'ERROR' && !l.seen_status) l.seen_status = 'acked' })
  } catch { /* silent */ } finally { bulkWorking.value = false }
}

watch(level, load)
onMounted(load)
</script>
