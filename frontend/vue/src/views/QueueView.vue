<!-- frontend/vue/src/views/QueueView.vue -->
<template>
  <div class="space-y-4">

    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-2">
      <!-- Status tabs -->
      <div class="flex bg-[var(--bg2)] border border-[var(--border)] rounded-lg p-0.5">
        <button
          v-for="f in statusFilters"
          :key="f.value"
          class="px-3 py-1.5 text-xs rounded transition-colors"
          :class="statusFilter === f.value ? 'bg-[var(--accent)] text-white' : 'text-[var(--muted)] hover:text-white'"
          @click="setFilter(f.value)"
        >{{ f.label }}</button>
      </div>

      <!-- Search -->
      <input
        v-model="search"
        :placeholder="t('common.search')"
        class="bg-[var(--bg2)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm w-48 focus:outline-none focus:border-[var(--accent)]"
      />

      <div class="ml-auto flex items-center gap-2">
        <span class="text-sm text-[var(--muted)]">{{ total }} {{ t('common.items') }}</span>

        <!-- View toggle -->
        <button :class="['w-8 h-8 rounded border flex items-center justify-center transition-colors', !gridView ? 'bg-[var(--accent)] border-[var(--accent)]' : 'bg-[var(--bg2)] border-[var(--border)] text-[var(--muted)] hover:text-white']" @click="gridView = false">
          <i class="fas fa-list text-xs" />
        </button>
        <button :class="['w-8 h-8 rounded border flex items-center justify-center transition-colors', gridView ? 'bg-[var(--accent)] border-[var(--accent)]' : 'bg-[var(--bg2)] border-[var(--border)] text-[var(--muted)] hover:text-white']" @click="gridView = true">
          <i class="fas fa-th text-xs" />
        </button>

        <!-- Purge button -->
        <button
          class="flex items-center gap-1.5 px-3 py-1.5 bg-[var(--bg2)] border border-[var(--border)] hover:border-red-500/50 hover:text-red-400 rounded-lg text-xs text-[var(--muted)] transition-colors"
          @click="confirmPurge = true"
        >
          <i class="fas fa-trash-alt text-xs" /> {{ t('queue.purgeDeleted') }}
        </button>
      </div>
    </div>

    <div v-if="error" class="bg-red-500/10 border border-red-500/30 text-red-400 rounded-lg px-4 py-3 text-sm">{{ error }}</div>

    <!-- Loading -->
    <div v-if="loading" class="text-center py-12 text-[var(--muted)]"><i class="fas fa-spinner fa-spin text-2xl" /></div>

    <template v-else>
      <!-- LIST VIEW -->
      <div v-if="!gridView" class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-xs text-[var(--muted)] border-b border-[var(--border)]">
              <th class="w-14 px-3 py-2" />
              <th class="text-left px-4 py-2">
                <SortHeader :label="t('queue.columns.title')" field="title" :sort="sort" :dir="dir" @sort="setSort" />
              </th>
              <th class="text-left px-4 py-2 hidden md:table-cell">
                <SortHeader :label="t('queue.columns.server')" field="library_id" :sort="sort" :dir="dir" @sort="setSort" />
              </th>
              <th class="text-left px-4 py-2 hidden md:table-cell">
                <SortHeader :label="t('queue.columns.library')" field="library_name" :sort="sort" :dir="dir" @sort="setSort" />
              </th>
              <th class="text-left px-4 py-2 hidden lg:table-cell">
                <SortHeader :label="t('queue.columns.requester')" field="seerr_username" :sort="sort" :dir="dir" @sort="setSort" />
              </th>
              <th class="text-left px-4 py-2 hidden xl:table-cell">
                <SortHeader :label="t('queue.columns.lastPlayed')" field="last_played" :sort="sort" :dir="dir" @sort="setSort" />
              </th>
              <th class="text-left px-4 py-2 hidden xl:table-cell">
                <SortHeader :label="t('queue.columns.addedAt')" field="added_date" :sort="sort" :dir="dir" @sort="setSort" />
              </th>
              <th class="text-left px-4 py-2">
                <SortHeader :label="t('queue.columns.status')" field="status" :sort="sort" :dir="dir" @sort="setSort" />
              </th>
              <th class="text-left px-4 py-2">
                <SortHeader :label="t('queue.columns.deletion')" field="delete_at" :sort="sort" :dir="dir" @sort="setSort" />
              </th>
              <th class="w-24 px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in items"
              :key="item.id"
              class="border-b border-[var(--border)] hover:bg-[var(--bg3)] transition-colors"
              :class="rowUrgencyClass(item.delete_at, item.status)"
            >
              <!-- Poster -->
              <td class="px-3 py-1.5 w-14">
                <div class="relative w-10 h-14 rounded overflow-hidden bg-[var(--bg3)] flex items-center justify-center">
                  <img
                    v-if="item.poster_url"
                    :src="`/api/proxy/image?url=${encodeURIComponent(item.poster_url)}`"
                    :alt="item.title"
                    class="w-full h-full object-cover absolute inset-0"
                    loading="lazy"
                    @error="e => e.target.style.display = 'none'"
                  />
                  <i :class="['fas', isSeries(item.media_type) ? 'fa-tv' : 'fa-film', 'text-sm text-[var(--muted)] opacity-50']" />
                </div>
              </td>
              <!-- Title (clickable → Seerr) -->
              <td class="px-4 py-2 max-w-[180px] xl:max-w-xs">
                <a
                  v-if="item.seerr_request_url"
                  :href="item.seerr_request_url"
                  target="_blank"
                  class="font-medium truncate block hover:text-[var(--accent)] transition-colors"
                  :title="item.title"
                >{{ item.title }}</a>
                <span v-else class="font-medium truncate block" :title="item.title">{{ item.title }}</span>
              </td>
              <!-- Server -->
              <td class="px-4 py-2 hidden md:table-cell">
                <span v-if="serverForItem(item)" class="flex items-center gap-1.5">
                  <span class="w-1.5 h-1.5 rounded-full flex-shrink-0"
                    :class="{
                      'bg-orange-400': serverForItem(item)?.type === 'plex',
                      'bg-green-500':  serverForItem(item)?.type === 'emby',
                      'bg-violet-500': serverForItem(item)?.type === 'jellyfin',
                      'bg-[var(--muted)]': !serverForItem(item)?.type,
                    }" />
                  <span class="text-xs text-[var(--muted)] truncate max-w-[80px]">{{ serverForItem(item)?.name }}</span>
                </span>
                <span v-else class="text-xs text-[var(--muted)]">—</span>
              </td>
              <!-- Library -->
              <td class="px-4 py-2 text-[var(--muted)] hidden md:table-cell truncate max-w-[120px] text-xs">{{ item.library_name || '—' }}</td>
              <!-- Requester (clickable → Seerr profile) -->
              <td class="px-4 py-2 hidden lg:table-cell">
                <a
                  v-if="item.seerr_user_id && seerrExternalUrl"
                  :href="`${seerrExternalUrl}/users/${item.seerr_user_id}`"
                  target="_blank"
                  class="text-[var(--muted)] hover:text-[var(--accent)] transition-colors"
                >{{ item.seerr_username || '—' }}</a>
                <span v-else class="text-[var(--muted)]">{{ item.seerr_username || '—' }}</span>
              </td>
              <!-- Last played -->
              <td
class="px-4 py-2 text-xs hidden xl:table-cell whitespace-nowrap"
                  :class="item.last_played ? 'text-[var(--muted)]' : 'text-red-400 font-medium'">
                {{ item.last_played ? formatDate(item.last_played) : t('queue.neverWatched') }}
              </td>
              <!-- Detected at -->
              <td class="px-4 py-2 text-xs hidden xl:table-cell whitespace-nowrap text-[var(--muted)]">
                {{ item.added_date ? formatDate(item.added_date) : '—' }}
              </td>
              <!-- Status -->
              <td class="px-4 py-2">
                <span class="px-2 py-0.5 rounded text-xs whitespace-nowrap" :class="statusClass(item.status)">
                  {{ statusLabel(item.status) }}
                </span>
              </td>
              <!-- Days -->
              <td class="px-4 py-2 whitespace-nowrap">
                <span :class="daysClass(item.delete_at, item.status)" class="text-xs font-semibold block">
                  {{ daysLabel(item.delete_at, item.status) }}
                </span>
                <span class="text-xs text-[var(--muted)]">{{ formatDate(item.delete_at) }}</span>
              </td>
              <!-- Actions -->
              <td class="px-3 py-2">
                <div v-if="item.status === 'pending'" class="flex items-center gap-1.5">
                  <button
                    :title="t('queue.deleteNow')"
                    class="w-7 h-7 rounded flex items-center justify-center text-[var(--muted)] hover:text-red-400 hover:bg-red-500/10 transition-colors"
                    @click="triggerDelete(item)"
                  ><i class="fas fa-trash text-xs" /></button>
                  <button
                    :title="t('queue.ignoreTitle')"
                    class="w-7 h-7 rounded flex items-center justify-center text-[var(--muted)] hover:text-yellow-400 hover:bg-yellow-500/10 transition-colors"
                    @click="openIgnore(item)"
                  ><i class="fas fa-ban text-xs" /></button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- GRID VIEW -->
      <div v-else class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-3">
        <div
          v-for="item in items"
          :key="item.id"
          class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden group relative"
          :class="item.status !== 'pending' ? 'opacity-60' : ''"
        >
          <!-- Poster -->
          <div class="relative aspect-[2/3] bg-[var(--bg3)] flex items-center justify-center overflow-hidden">
            <img
              v-if="item.poster_url"
              :src="`/api/proxy/image?url=${encodeURIComponent(item.poster_url)}`"
              :alt="item.title"
              class="w-full h-full object-cover"
              loading="lazy"
              @error="e => e.target.style.display = 'none'"
            />
            <i :class="['fas', isSeries(item.media_type) ? 'fa-tv' : 'fa-film', 'text-3xl text-[var(--muted)] opacity-30']" />

            <!-- Bottom banner (Emby style) -->
            <div class="absolute bottom-0 inset-x-0 py-1.5 px-2 text-center text-xs font-bold text-white" :class="gridBannerClass(item.delete_at, item.status)">
              {{ daysLabel(item.delete_at, item.status) }}
            </div>

            <!-- Actions overlay (on hover) -->
            <div v-if="item.status === 'pending'" class="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-3 pb-8">
              <button :title="t('common.delete')" class="w-9 h-9 rounded-full bg-red-500 hover:bg-red-600 flex items-center justify-center transition-colors" @click.stop="triggerDelete(item)">
                <i class="fas fa-trash text-sm text-white" />
              </button>
              <button :title="t('queue.ignoreTitle')" class="w-9 h-9 rounded-full bg-yellow-500 hover:bg-yellow-600 flex items-center justify-center transition-colors" @click.stop="openIgnore(item)">
                <i class="fas fa-ban text-sm text-white" />
              </button>
            </div>
          </div>

          <!-- Info -->
          <div class="p-2">
            <a
v-if="item.seerr_request_url" :href="item.seerr_request_url" target="_blank"
              class="text-xs font-medium truncate block hover:text-[var(--accent)] transition-colors" :title="item.title">
              {{ item.title }}
            </a>
            <span v-else class="text-xs font-medium truncate block" :title="item.title">{{ item.title }}</span>
            <div class="text-[10px] text-[var(--muted)] truncate">{{ item.library_name }}</div>
          </div>
        </div>
      </div>
    </template>

    <!-- Pagination -->
    <div v-if="totalPages > 1" class="flex items-center justify-center gap-1">
      <button :disabled="page === 1" class="w-8 h-8 rounded text-sm text-[var(--muted)] hover:bg-[var(--bg3)] disabled:opacity-30" @click="page = 1">«</button>
      <button
        v-for="p in visiblePages" :key="p"
        class="w-8 h-8 rounded text-sm transition-colors"
        :class="p === page ? 'bg-[var(--accent)] text-white' : p === '…' ? 'cursor-default text-[var(--muted)]' : 'text-[var(--muted)] hover:bg-[var(--bg3)]'"
        @click="typeof p === 'number' && (page = p)"
      >{{ p }}</button>
      <button :disabled="page === totalPages" class="w-8 h-8 rounded text-sm text-[var(--muted)] hover:bg-[var(--bg3)] disabled:opacity-30" @click="page = totalPages">»</button>
    </div>

    <!-- Ignore modal -->
    <ConfirmModal
      :show="!!ignoreTarget"
      :confirm-label="t('queue.ignoreTitle')"
      confirm-class="bg-yellow-500 hover:bg-yellow-600 text-white"
      @confirm="doIgnore"
      @cancel="ignoreTarget = null"
    >
      <div class="space-y-4">
        <h3 class="font-semibold">{{ t('queue.ignoreTitle') }} — {{ ignoreTarget?.title }}</h3>
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">{{ t('queue.ignoreReason') }}</label>
          <input v-model="ignoreReason" type="text" :placeholder="t('queue.reasonPlaceholder')" class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]" />
        </div>
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">{{ t('queue.expiration') }}</label>
          <input v-model.number="ignoreDays" type="number" min="0" class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]" />
        </div>
      </div>
    </ConfirmModal>

    <!-- Delete confirm modal -->
    <ConfirmModal
      :show="!!deleteTarget"
      :message="deleteTarget ? `${t('queue.confirmDelete').replace('?', '')} ${deleteTarget.title} ?` : ''"
      :confirm-label="t('common.delete')"
      @confirm="doDelete"
      @cancel="deleteTarget = null"
    />

    <!-- Purge confirm modal -->
    <ConfirmModal
      :show="confirmPurge"
      :message="t('queue.confirmPurge')"
      :confirm-label="t('queue.purge')"
      @confirm="doPurge"
      @cancel="confirmPurge = false"
    />
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import api from '@/api/client'
import { useSettingsStore } from '@/stores/settings'
import { useServersStore } from '@/stores/servers'
import SortHeader   from '@/components/ui/SortHeader.vue'
import ConfirmModal from '@/components/ui/ConfirmModal.vue'

const { t } = useI18n()
const settings = useSettingsStore()
const serversStore = useServersStore()

function serverForItem(item) {
  const lib = serversStore.libraries.find(l => l.id === item.library_id)
  if (!lib) return null
  return serversStore.servers.find(s => String(s.id) === String(lib.server_id))
}

const statusFilters = computed(() => [
  { value: '',        label: t('queue.filters.all') },
  { value: 'pending', label: t('queue.filters.pending') },
  { value: 'deleted', label: t('queue.filters.deleted') },
  { value: 'error',   label: t('queue.filters.errors') },
])

const items        = ref([])
const total        = ref(0)
const page         = ref(1)
const statusFilter = ref('')
const search       = ref('')
const sort         = ref('delete_at')
const dir          = ref('asc')
const loading      = ref(false)
const error        = ref('')
const gridView     = ref(false)
const perPage      = 50

const ignoreTarget = ref(null)
const ignoreReason = ref('')
const ignoreDays   = ref(0)
const deleteTarget = ref(null)
const confirmPurge = ref(false)

const seerrExternalUrl = computed(() => {
  const s = settings.settings.seerr_external_url || ''
  return s.replace(/\/$/, '')
})

const totalPages = computed(() => Math.ceil(total.value / perPage))

const visiblePages = computed(() => {
  const n = totalPages.value, p = page.value
  if (n <= 7) return Array.from({ length: n }, (_, i) => i + 1)
  const start = Math.max(1, p - 2), end = Math.min(n, p + 2)
  const pages = []
  if (start > 2)        pages.push(1, '…')
  else if (start === 2) pages.push(1)
  for (let i = start; i <= end; i++) pages.push(i)
  if (end < n - 1)      pages.push('…', n)
  else if (end === n - 1) pages.push(n)
  return pages
})

function setSort(field) {
  if (sort.value === field) dir.value = dir.value === 'asc' ? 'desc' : 'asc'
  else { sort.value = field; dir.value = 'asc' }
  page.value = 1
  load()
}
function setFilter(v) { statusFilter.value = v; page.value = 1 }

const STATUS_CLASSES = {
  pending: 'bg-yellow-500/20 text-yellow-400',
  deleted: 'bg-green-500/20 text-green-400',
  error:   'bg-red-700/20 text-red-300',
}
function statusLabel(s) {
  const map = { pending: t('status.pending'), deleted: t('status.deleted'), error: t('status.error') }
  return map[s] || s
}
function statusClass(s) { return STATUS_CLASSES[s] || 'bg-[var(--bg3)] text-[var(--muted)]' }
function isSeries(tp) { return tp === 'Episode' || tp === 'Series' || tp === 'Season' }

function daysRemaining(deleteAt) {
  if (!deleteAt) return null
  return Math.ceil((new Date(deleteAt) - new Date()) / (1000 * 60 * 60 * 24))
}
function daysLabel(deleteAt, status) {
  if (status === 'deleted') return t('status.deleted')
  if (!deleteAt) return '—'
  const d = daysRemaining(deleteAt)
  if (d === null) return '—'
  if (d < 0)  return t('days.exceeded')
  if (d === 0) return t('days.today')
  if (d === 1) return t('days.tomorrow')
  return t('days.inDays', { n: d })
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
function gridBannerClass(deleteAt, status) {
  if (status === 'deleted') return 'bg-green-600/90'
  if (status === 'error')   return 'bg-red-700/90'
  const d = daysRemaining(deleteAt)
  if (d === null) return 'bg-[var(--bg3)]/80'
  if (d <= 3)  return 'bg-red-600/90'
  if (d <= 7)  return 'bg-orange-600/90'
  if (d <= 14) return 'bg-yellow-600/90'
  return 'bg-black/50'
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

async function load() {
  loading.value = true; error.value = ''
  try {
    const params = { limit: perPage, offset: (page.value - 1) * perPage, sort: sort.value, dir: dir.value }
    if (statusFilter.value) params.status = statusFilter.value
    if (search.value) params.search = search.value
    const { data } = await api.get('/media', { params })
    items.value = data.items || data || []
    total.value = data.total  || items.value.length
  } catch { error.value = t('queue.error.loadFailed') }
  finally { loading.value = false }
}

function openIgnore(item) {
  ignoreTarget.value = item
  ignoreReason.value = ''
  ignoreDays.value   = 0
}
async function doIgnore() {
  if (!ignoreTarget.value) return
  await api.post(`/media/${ignoreTarget.value.id}/ignore`, null, {
    params: {
      reason:      ignoreReason.value || undefined,
      expire_days: ignoreDays.value > 0 ? ignoreDays.value : undefined,
    },
  })
  ignoreTarget.value = null
  load()
}

function triggerDelete(item) { deleteTarget.value = item }
async function doDelete() {
  if (!deleteTarget.value) return
  await api.post(`/media/${deleteTarget.value.id}/delete-now`)
  deleteTarget.value = null
  load()
}

async function doPurge() {
  confirmPurge.value = false
  await api.delete('/media/purge/deleted')
  load()
}

let searchTimer = null
watch(search, () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { page.value = 1; load() }, 300) })
watch([statusFilter, page], load)
onMounted(async () => { await settings.fetch(); load() })
</script>
