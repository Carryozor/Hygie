<!-- frontend/vue/src/views/PublicView.vue — no-auth public dashboard -->
<template>
  <div class="min-h-screen bg-[var(--bg1)] text-[var(--text)] p-4 md:p-8">
    <!-- Header -->
    <div class="flex items-center justify-between mb-8">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-full bg-[var(--accent)]/20 flex items-center justify-center">
          <i class="fas fa-film text-[var(--accent)]" />
        </div>
        <div>
          <h1 class="text-xl font-bold">Hygie</h1>
          <p class="text-xs text-[var(--muted)]">Prochaines suppressions</p>
        </div>
      </div>
      <span class="text-xs text-[var(--muted)]">{{ totalUpcoming }} média(s) planifié(s)</span>
    </div>

    <!-- Disabled message -->
    <div v-if="disabled" class="text-center py-20 text-[var(--muted)]">
      <i class="fas fa-lock text-4xl mb-4 block opacity-30" />
      <p class="text-sm">Le tableau de bord public n'est pas activé.</p>
    </div>

    <template v-else-if="!loading">
      <!-- Calendar -->
      <div class="mb-8">
        <!-- Month nav -->
        <div class="flex items-center justify-between mb-4">
          <button class="w-8 h-8 rounded-lg bg-[var(--bg2)] border border-[var(--border)] hover:bg-[var(--bg3)] transition-colors flex items-center justify-center" @click="prevMonth">
            <i class="fas fa-chevron-left text-xs" />
          </button>
          <div class="flex items-center gap-2">
            <h2 class="font-semibold capitalize">{{ monthLabel }}</h2>
            <button
v-if="!isCurrentMonth" class="text-xs px-2 py-0.5 rounded-full bg-[var(--accent)]/20 text-[var(--accent)] hover:bg-[var(--accent)]/30 transition-colors"
              @click="goToToday">
              Aujourd'hui
            </button>
          </div>
          <button class="w-8 h-8 rounded-lg bg-[var(--bg2)] border border-[var(--border)] hover:bg-[var(--bg3)] transition-colors flex items-center justify-center" @click="nextMonth">
            <i class="fas fa-chevron-right text-xs" />
          </button>
        </div>

        <div class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden">
          <div class="grid grid-cols-7 border-b border-[var(--border)]">
            <div v-for="d in DAYS" :key="d" class="text-center text-xs font-semibold py-2 text-[var(--muted)] uppercase tracking-wide">{{ d }}</div>
          </div>
          <div>
            <div v-for="(week, wi) in grid" :key="wi" class="grid grid-cols-7 border-b border-[var(--border)] last:border-b-0">
              <div
                v-for="(cell, di) in week"
                :key="di"
                class="min-h-[70px] border-r border-[var(--border)] last:border-r-0 p-1"
                :class="cell && cell.events.length ? 'cursor-pointer hover:bg-[var(--bg3)]' : (!cell ? 'bg-[var(--bg3)]/30' : '')"
                @click="cell && cell.events.length && openDay(cell)"
              >
                <template v-if="cell">
                  <div
class="w-6 h-6 rounded-full flex items-center justify-center text-xs font-semibold mb-1"
                    :class="isToday(cell) ? 'bg-[var(--accent)] text-white' : isPast(cell) ? 'text-[var(--muted)]' : 'text-[var(--text)]'">
                    {{ cell.day }}
                  </div>
                  <div v-if="cell.events.length" class="space-y-0.5">
                    <div
v-for="item in cell.events.slice(0, 2)" :key="item.id"
                      class="truncate text-[10px] px-1 py-0.5 rounded bg-[var(--accent)]/20 text-[var(--accent)]"
                      :title="item.title">{{ item.title }}</div>
                    <div v-if="cell.events.length > 2" class="text-[10px] text-[var(--muted)] px-1">+{{ cell.events.length - 2 }} autres</div>
                  </div>
                </template>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Selected day panel (inline below calendar) -->
      <transition name="slide-down">
        <div v-if="selectedDay" class="mt-4 bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden">
          <div class="flex items-center justify-between px-5 py-3 border-b border-[var(--border)]">
            <h3 class="font-semibold capitalize text-sm">{{ selectedDayLabel }}</h3>
            <button class="text-[var(--muted)] hover:text-white w-7 h-7 flex items-center justify-center rounded hover:bg-[var(--bg3)] transition-colors" @click="selectedDay = null">
              <i class="fas fa-times text-xs" />
            </button>
          </div>
          <div class="divide-y divide-[var(--border)]">
            <div
v-for="item in selectedDay.events" :key="item.id"
              class="flex items-center gap-4 px-5 py-3 group"
              :class="mediaLink(item) ? 'cursor-pointer hover:bg-[var(--bg3)] transition-colors' : ''"
              @click="mediaLink(item) && openMedia(item)">
              <div class="relative w-9 h-12 rounded overflow-hidden bg-[var(--bg3)] flex-shrink-0 flex items-center justify-center">
                <img
v-if="item.poster_url"
                  :src="`/api/proxy/image?url=${encodeURIComponent(item.poster_url)}`"
                  :alt="item.title"
                  class="w-full h-full object-cover absolute inset-0"
                  loading="lazy"
                  @error="e => e.target.style.display = 'none'" />
                <i :class="['fas', item.media_type === 'Episode' || item.media_type === 'Series' ? 'fa-tv' : 'fa-film', 'text-xs text-[var(--muted)] opacity-50']" />
              </div>
              <div class="flex-1 min-w-0">
                <div class="font-medium text-sm truncate flex items-center gap-1.5">
                  {{ item.title }}
                  <i v-if="mediaLink(item)" class="fas fa-external-link-alt text-[10px] text-[var(--muted)] opacity-0 group-hover:opacity-100 transition-opacity" />
                </div>
                <div class="text-xs text-[var(--muted)]">{{ item.library_name }}</div>
              </div>
            </div>
          </div>
        </div>
      </transition>

      <!-- Libraries -->
      <div v-if="libraries.length" class="mt-8">
        <h2 class="font-semibold text-sm mb-3 text-[var(--muted)] uppercase tracking-widest">Bibliothèques</h2>
        <div class="flex flex-wrap gap-2">
          <span
v-for="lib in libraries" :key="lib.id"
            class="flex items-center gap-2 px-3 py-1.5 bg-[var(--bg2)] border border-[var(--border)] rounded-lg text-sm">
            <i class="fas fa-layer-group text-[var(--accent)] text-xs" />
            {{ lib.name }}
          </span>
        </div>
      </div>
    </template>

    <div v-else class="text-center py-20 text-[var(--muted)]">
      <i class="fas fa-spinner fa-spin text-2xl" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'

const DAYS = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim']

const events     = ref({})
const libraries  = ref([])
const loading    = ref(true)
const disabled   = ref(false)
const selectedDay = ref(null)

const today     = new Date()
const viewYear  = ref(today.getFullYear())
const viewMonth = ref(today.getMonth())

const monthLabel = computed(() =>
  new Date(viewYear.value, viewMonth.value, 1)
    .toLocaleDateString('fr-FR', { month: 'long', year: 'numeric' })
)

const selectedDayLabel = computed(() => {
  if (!selectedDay.value) return ''
  return new Date(selectedDay.value.dateStr + 'T12:00:00')
    .toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
})

const totalUpcoming = computed(() => Object.values(events.value).reduce((s, arr) => s + arr.length, 0))

const grid = computed(() => {
  const y = viewYear.value, m = viewMonth.value
  const firstDay = new Date(y, m, 1)
  const lastDate = new Date(y, m + 1, 0).getDate()
  let offset = firstDay.getDay()
  offset = offset === 0 ? 6 : offset - 1
  const weeks = []
  let day = 1
  for (let w = 0; w < 6; w++) {
    const row = []
    for (let d = 0; d < 7; d++) {
      const idx = w * 7 + d
      if (idx < offset || day > lastDate) { row.push(null) }
      else {
        const dateStr = `${y}-${String(m + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`
        row.push({ day, dateStr, events: events.value[dateStr] || [] })
        day++
      }
    }
    weeks.push(row)
    if (day > lastDate) break
  }
  return weeks
})

const isCurrentMonth = computed(() =>
  viewYear.value === today.getFullYear() && viewMonth.value === today.getMonth()
)

function isToday(cell) {
  return cell.day === today.getDate() && viewMonth.value === today.getMonth() && viewYear.value === today.getFullYear()
}
function isPast(cell) {
  return new Date(cell.dateStr + 'T23:59:59') < today
}
function openDay(cell) { selectedDay.value = cell }
function prevMonth() {
  selectedDay.value = null
  if (viewMonth.value === 0) { viewMonth.value = 11; viewYear.value-- } else viewMonth.value--
}
function nextMonth() {
  selectedDay.value = null
  if (viewMonth.value === 11) { viewMonth.value = 0; viewYear.value++ } else viewMonth.value++
}
function goToToday() {
  selectedDay.value = null
  viewYear.value  = today.getFullYear()
  viewMonth.value = today.getMonth()
}

function mediaLink(item) {
  if (item.tmdb_id) {
    const isTv = item.media_type === 'Series' || item.media_type === 'Episode'
    return `https://www.themoviedb.org/${isTv ? 'tv' : 'movie'}/${item.tmdb_id}`
  }
  return null
}
function openMedia(item) {
  const url = mediaLink(item)
  if (url) window.open(url, '_blank', 'noopener')
}

function formatBytes(b) {
  if (!b) return ''
  if (b >= 1e12) return (b / 1e12).toFixed(1) + ' To'
  if (b >= 1e9)  return (b / 1e9).toFixed(1) + ' Go'
  if (b >= 1e6)  return (b / 1e6).toFixed(1) + ' Mo'
  return b + ' o'
}

onMounted(async () => {
  try {
    const res = await fetch('/api/public/upcoming')
    if (res.status === 403) { disabled.value = true; return }
    const data = await res.json()
    events.value = data.events || {}
    libraries.value = data.libraries || []
  } catch { disabled.value = true }
  finally { loading.value = false }
})
</script>

<style scoped>
.slide-down-enter-active, .slide-down-leave-active { transition: all 0.2s ease; }
.slide-down-enter-from, .slide-down-leave-to { opacity: 0; transform: translateY(-8px); }
</style>
