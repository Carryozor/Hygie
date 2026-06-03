<!-- frontend/vue/src/views/QueueView.vue -->
<template>
  <div class="space-y-4">

    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-2">
      <div class="flex bg-[var(--bg2)] border border-[var(--border)] rounded-lg p-0.5">
        <button
          v-for="f in statusFiltersOptions"
          :key="f.value"
          class="px-3 py-1.5 text-xs rounded transition-colors"
          :class="statusFilter === f.value ? 'bg-[var(--accent)] text-white' : 'text-[var(--muted)] hover:text-white'"
          @click="setFilter(f.value)"
        >{{ f.label }}</button>
      </div>

      <input
        v-model="search"
        :placeholder="t('common.search')"
        class="bg-[var(--bg2)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm w-48 focus:outline-none focus:border-[var(--accent)]"
      />

      <div class="ml-auto flex items-center gap-2">
        <span class="text-sm text-[var(--muted)]">{{ total }} {{ t('common.items') }}</span>

        <button
          :class="['w-8 h-8 rounded border flex items-center justify-center transition-colors',
            !gridView ? 'bg-[var(--accent)] border-[var(--accent)]' : 'bg-[var(--bg2)] border-[var(--border)] text-[var(--muted)] hover:text-white']"
          @click="gridView = false"
        ><i class="fas fa-list text-xs" /></button>
        <button
          :class="['w-8 h-8 rounded border flex items-center justify-center transition-colors',
            gridView ? 'bg-[var(--accent)] border-[var(--accent)]' : 'bg-[var(--bg2)] border-[var(--border)] text-[var(--muted)] hover:text-white']"
          @click="gridView = true"
        ><i class="fas fa-table-cells text-xs" /></button>

        <button
          class="flex items-center gap-1.5 px-3 py-1.5 bg-[var(--bg2)] border border-[var(--border)] hover:border-red-500/50 hover:text-red-400 rounded-lg text-xs text-[var(--muted)] transition-colors"
          @click="confirmPurge = true"
        >
          <i class="fas fa-trash-can text-xs" /> {{ t('queue.purgeDeleted') }}
        </button>
      </div>
    </div>

    <div v-if="error" class="bg-red-500/10 border border-red-500/30 text-red-400 rounded-lg px-4 py-3 text-sm">{{ error }}</div>
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
            <QueueListRow
              v-for="item in items"
              :key="item.id"
              :item="item"
              :server="serverForItem(item)"
              :seerr-external-url="seerrExternalUrl"
              :days-label="daysLabel"
              :days-class="daysClass"
              :row-urgency-class="rowUrgencyClass"
              :format-date="formatDate"
              :status-label="statusLabel"
              :status-class="statusClass"
              :is-series="isSeries"
              @delete="triggerDelete"
              @ignore="openIgnore"
            />
          </tbody>
        </table>
      </div>

      <!-- GRID VIEW -->
      <div v-else class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-3">
        <QueueGridCard
          v-for="item in items"
          :key="item.id"
          :item="item"
          :days-label="daysLabel"
          :grid-banner-class="gridBannerClass"
          :is-series="isSeries"
          @delete="triggerDelete"
          @ignore="openIgnore"
        />
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
import { useQueueItems } from '@/composables/useQueueItems'
import SortHeader    from '@/components/ui/SortHeader.vue'
import ConfirmModal  from '@/components/ui/ConfirmModal.vue'
import QueueListRow  from '@/components/queue/QueueListRow.vue'
import QueueGridCard from '@/components/queue/QueueGridCard.vue'

const { t } = useI18n()
const settings = useSettingsStore()
const serversStore = useServersStore()

const {
  items, total, page, statusFilter, search, sort, dir,
  loading, error, totalPages, visiblePages,
  load, setSort, setFilter,
  daysLabel, daysClass, gridBannerClass, rowUrgencyClass,
  formatDate, statusLabel, statusClass, isSeries,
} = useQueueItems()

const gridView     = ref(false)
const ignoreTarget = ref(null)
const ignoreReason = ref('')
const ignoreDays   = ref(0)
const deleteTarget = ref(null)
const confirmPurge = ref(false)

const statusFiltersOptions = computed(() => [
  { value: '',        label: t('queue.filters.all') },
  { value: 'pending', label: t('queue.filters.pending') },
  { value: 'deleted', label: t('queue.filters.deleted') },
  { value: 'error',   label: t('queue.filters.errors') },
])

const seerrExternalUrl = computed(() => (settings.settings.seerr_external_url || '').replace(/\/$/, ''))

function serverForItem(item) {
  const lib = serversStore.libraries.find(l => l.id === item.library_id)
  if (!lib) return null
  return serversStore.servers.find(s => String(s.id) === String(lib.server_id))
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
