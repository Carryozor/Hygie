<!-- frontend/vue/src/views/CalendarView.vue -->
<template>
  <div class="space-y-4">

    <!-- Month navigation -->
    <div class="flex items-center justify-between">
      <button class="w-8 h-8 rounded-lg bg-[var(--bg2)] border border-[var(--border)] hover:bg-[var(--bg3)] transition-colors flex items-center justify-center" @click="prevMonth">
        <i class="fas fa-chevron-left text-xs" />
      </button>
      <div class="flex items-center gap-3">
        <h2 class="font-semibold capitalize">{{ monthLabel }}</h2>
        <button
          v-if="!isCurrentMonth"
          class="px-2.5 py-1 text-xs rounded-lg bg-[var(--accent)]/10 border border-[var(--accent)]/30 text-[var(--accent)] hover:bg-[var(--accent)]/20 transition-colors"
          @click="goToToday"
        >{{ t('common.today') }}</button>
      </div>
      <button class="w-8 h-8 rounded-lg bg-[var(--bg2)] border border-[var(--border)] hover:bg-[var(--bg3)] transition-colors flex items-center justify-center" @click="nextMonth">
        <i class="fas fa-chevron-right text-xs" />
      </button>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="text-center py-12 text-[var(--muted)]">
      <i class="fas fa-spinner fa-spin text-2xl" />
    </div>

    <template v-else>
      <!-- Calendar grid -->
      <div class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden">
        <!-- Day headers -->
        <div class="grid grid-cols-7 border-b border-[var(--border)]">
          <div v-for="d in dayHeaders" :key="d" class="text-center text-xs font-semibold py-2 text-[var(--muted)] uppercase tracking-wide">
            {{ d }}
          </div>
        </div>

        <!-- Weeks -->
        <div>
          <div v-for="(week, wi) in grid" :key="wi" class="grid grid-cols-7 border-b border-[var(--border)] last:border-b-0">
            <div
              v-for="(cell, di) in week"
              :key="di"
              class="min-h-[80px] border-r border-[var(--border)] last:border-r-0 p-1"
              :class="cellClass(cell)"
              @click="cell && openDay(cell)"
            >
              <template v-if="cell">
                <!-- Day number -->
                <div
                  class="w-6 h-6 rounded-full flex items-center justify-center text-xs font-semibold mb-1"
                  :class="dayNumberClass(cell)"
                >
                  {{ cell.day }}
                </div>
                <!-- Event dots / count -->
                <div v-if="cell.events.length" class="space-y-0.5">
                  <div
                    v-for="item in cell.events.slice(0, 3)"
                    :key="item.id"
                    class="truncate text-[10px] px-1 py-0.5 rounded"
                    :class="eventChipClass(cell)"
                    :title="item.title"
                  >{{ item.title }}</div>
                  <div v-if="cell.events.length > 3" class="text-[10px] text-[var(--muted)] px-1">
                    {{ t('calendar.more', { n: cell.events.length - 3 }) }}
                  </div>
                </div>
              </template>
            </div>
          </div>
        </div>
      </div>

      <!-- Summary bar -->
      <div class="text-xs text-[var(--muted)] text-right">
        {{ totalThisMonth }} {{ t('calendar.summary') }}
      </div>

      <!-- Day detail panel (inline, below calendar) -->
      <div v-if="selectedDay" class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden">
        <!-- Panel header -->
        <div class="flex items-center justify-between px-5 py-3 border-b border-[var(--border)]">
          <h3 class="font-semibold capitalize text-sm">{{ selectedDayLabel }}</h3>
          <button class="text-[var(--muted)] hover:text-white transition-colors" @click="selectedDay = null">
            <i class="fas fa-xmark text-xs" />
          </button>
        </div>
        <!-- Items -->
        <div class="divide-y divide-[var(--border)]">
          <div
            v-for="item in selectedDay.events"
            :key="item.id"
            class="flex items-center gap-4 px-5 py-3 hover:bg-[var(--bg3)] transition-colors"
          >
            <!-- Poster -->
            <div class="relative w-10 h-14 rounded overflow-hidden bg-[var(--bg3)] flex-shrink-0 flex items-center justify-center">
              <img
                v-if="item.poster_url"
                :src="`/api/proxy/image?url=${encodeURIComponent(item.poster_url)}`"
                :alt="item.title"
                class="w-full h-full object-cover absolute inset-0"
                loading="lazy"
                @error="e => e.target.style.display = 'none'"
              />
              <i :class="['fas', item.media_type === 'Episode' || item.media_type === 'Series' ? 'fa-tv' : 'fa-film', 'text-sm text-[var(--muted)] opacity-50']" />
            </div>
            <!-- Info -->
            <div class="flex-1 min-w-0">
              <a
                v-if="item.seerr_request_url"
                :href="safeUrl(item.seerr_request_url)"
                target="_blank"
                rel="noopener noreferrer"
                class="font-medium text-sm truncate hover:text-[var(--accent)] transition-colors block"
              >{{ item.title }}</a>
              <div v-else class="font-medium text-sm truncate">{{ item.title }}</div>
              <div class="text-xs text-[var(--muted)]">
                {{ item.library_name }}
                <span v-if="item.seerr_username"> · {{ item.seerr_username }}</span>
              </div>
            </div>
            <!-- Type + days -->
            <div class="flex flex-col items-end gap-1 flex-shrink-0">
              <span class="text-xs px-2 py-0.5 rounded bg-[var(--bg3)] text-[var(--muted)]">
                {{ typeLabel(item.media_type) }}
              </span>
              <span v-if="item.delete_at" class="text-[10px]" :class="deleteSoonClass(item.delete_at)">
                {{ deleteSoonLabel(item.delete_at) }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import api from '@/api/client'
import { safeUrl } from '@/utils/safeUrl'

const { t, locale } = useI18n()

const dayHeaders = computed(() => [
  t('calendar.days.mon'),
  t('calendar.days.tue'),
  t('calendar.days.wed'),
  t('calendar.days.thu'),
  t('calendar.days.fri'),
  t('calendar.days.sat'),
  t('calendar.days.sun'),
])

function typeLabel(type) {
  const map = {
    Movie: t('media.movie'),
    Series: t('media.series'),
    Episode: t('media.episode'),
    Season: t('media.season'),
  }
  return map[type] || type
}

const today      = new Date()
const viewYear   = ref(today.getFullYear())
const viewMonth  = ref(today.getMonth()) // 0-indexed
const events     = ref({})
const loading    = ref(false)
const selectedDay = ref(null)

const monthLabel = computed(() =>
  new Date(viewYear.value, viewMonth.value, 1)
    .toLocaleDateString(locale.value, { month: 'long', year: 'numeric' })
)

const selectedDayLabel = computed(() => {
  if (!selectedDay.value) return ''
  return new Date(selectedDay.value.dateStr + 'T12:00:00')
    .toLocaleDateString(locale.value, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
})

const totalThisMonth = computed(() => {
  const prefix = `${viewYear.value}-${String(viewMonth.value + 1).padStart(2, '0')}`
  return Object.entries(events.value)
    .filter(([d]) => d.startsWith(prefix))
    .reduce((s, [, items]) => s + items.length, 0)
})

const grid = computed(() => {
  const y = viewYear.value
  const m = viewMonth.value
  const firstDay  = new Date(y, m, 1)
  const lastDate  = new Date(y, m + 1, 0).getDate()

  // 0=Mon offset
  let offset = firstDay.getDay()
  offset = offset === 0 ? 6 : offset - 1

  const weeks = []
  let day = 1
  for (let w = 0; w < 6; w++) {
    const row = []
    for (let d = 0; d < 7; d++) {
      const idx = w * 7 + d
      if (idx < offset || day > lastDate) {
        row.push(null)
      } else {
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

function isToday(cell) {
  if (!cell) return false
  const tp = today
  return cell.day === tp.getDate() && viewMonth.value === tp.getMonth() && viewYear.value === tp.getFullYear()
}

function isPast(cell) {
  if (!cell) return false
  const d = new Date(cell.dateStr + 'T23:59:59')
  return d < today
}

function daysFromToday(cell) {
  if (!cell) return null
  return Math.ceil((new Date(cell.dateStr + 'T12:00:00') - today) / 86400000)
}

function cellClass(cell) {
  if (!cell) return 'bg-[var(--bg3)]/30'
  if (cell.events.length === 0) return 'cursor-default'
  const d = daysFromToday(cell)
  if (d !== null && d <= 3 && d >= 0) return 'cursor-pointer hover:bg-red-500/10'
  if (d !== null && d <= 7 && d >= 0) return 'cursor-pointer hover:bg-orange-500/10'
  return 'cursor-pointer hover:bg-[var(--bg3)]'
}

function dayNumberClass(cell) {
  if (isToday(cell)) return 'bg-[var(--accent)] text-white'
  if (isPast(cell))  return 'text-[var(--muted)]'
  return 'text-[var(--text)]'
}

function eventChipClass(cell) {
  const d = daysFromToday(cell)
  if (d !== null && d <= 3) return 'bg-red-500/20 text-red-300'
  if (d !== null && d <= 7) return 'bg-orange-500/20 text-orange-300'
  return 'bg-[var(--accent)]/20 text-[var(--accent)]'
}

function openDay(cell) {
  if (!cell.events.length) return
  if (selectedDay.value?.dateStr === cell.dateStr) { selectedDay.value = null; return }
  selectedDay.value = cell
}

function deleteSoonClass(deleteAt) {
  const days = Math.ceil((new Date(deleteAt) - new Date()) / 86400000)
  if (days <= 3) return 'text-red-400'
  if (days <= 7) return 'text-orange-400'
  return 'text-[var(--muted)]'
}

function deleteSoonLabel(deleteAt) {
  const days = Math.ceil((new Date(deleteAt) - new Date()) / 86400000)
  if (days <= 0) return t('days.imminent')
  if (days === 1) return t('days.tomorrow')
  return t('days.inDays', { n: days })
}

const isCurrentMonth = computed(() =>
  viewYear.value === today.getFullYear() && viewMonth.value === today.getMonth()
)

function prevMonth() {
  if (viewMonth.value === 0) { viewMonth.value = 11; viewYear.value-- }
  else viewMonth.value--
}
function nextMonth() {
  if (viewMonth.value === 11) { viewMonth.value = 0; viewYear.value++ }
  else viewMonth.value++
}
function goToToday() {
  viewYear.value  = today.getFullYear()
  viewMonth.value = today.getMonth()
}

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/calendar', { params: { days_ahead: 365 } })
    events.value = data.events || {}
  } catch { /* silent */ }
  finally { loading.value = false }
}

watch([viewYear, viewMonth], load)
onMounted(load)
</script>
