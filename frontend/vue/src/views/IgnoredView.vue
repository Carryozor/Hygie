<!-- frontend/vue/src/views/IgnoredView.vue -->
<template>
  <div class="space-y-4">
    <div class="flex items-center gap-3 flex-wrap">
      <input
        v-model="search"
        :placeholder="t('common.search')"
        class="bg-[var(--bg2)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm w-56 focus:outline-none focus:border-[var(--accent)]"
      />
      <span class="text-sm text-[var(--muted)] ml-auto">{{ items.length }} {{ t('ignored.count') }}</span>
    </div>

    <div v-if="error" class="bg-red-500/10 border border-red-500/30 text-red-400 rounded-lg px-4 py-3 text-sm">{{ error }}</div>

    <div class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden">
      <div v-if="loading" class="p-8 text-center text-[var(--muted)]"><i class="fas fa-spinner fa-spin text-2xl" /></div>
      <div v-else-if="!items.length" class="p-8 text-center text-[var(--muted)] text-sm">
        <i class="fas fa-ban text-3xl mb-3 block opacity-30" />
        {{ t('ignored.empty') }}
      </div>
      <table v-else class="w-full text-sm">
        <thead>
          <tr class="text-xs text-[var(--muted)] border-b border-[var(--border)]">
            <th class="w-14 px-3 py-2" />
            <th class="text-left px-4 py-2">{{ t('ignored.columns.title') }}</th>
            <th class="text-left px-4 py-2 hidden md:table-cell">{{ t('ignored.columns.library') }}</th>
            <th class="text-left px-4 py-2 hidden lg:table-cell">{{ t('ignored.columns.reason') }}</th>
            <th class="text-left px-4 py-2 hidden lg:table-cell">{{ t('ignored.columns.ignoredAt') }}</th>
            <th class="text-left px-4 py-2 hidden xl:table-cell">{{ t('ignored.columns.expires') }}</th>
            <th class="w-24 px-3 py-2" />
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in items"
            :key="item.id"
            class="border-b border-[var(--border)] last:border-b-0 hover:bg-[var(--bg3)] transition-colors"
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
            <!-- Title -->
            <td class="px-4 py-2">
              <div class="font-medium truncate max-w-[180px]" :title="item.title">{{ item.title }}</div>
              <div class="text-xs text-[var(--muted)]">{{ typeLabel(item.media_type) }}</div>
            </td>
            <!-- Library -->
            <td class="px-4 py-2 text-[var(--muted)] hidden md:table-cell">{{ item.library_name || '—' }}</td>
            <!-- Reason -->
            <td class="px-4 py-2 text-[var(--muted)] text-xs hidden lg:table-cell max-w-[150px] truncate">
              {{ item.reason || '—' }}
            </td>
            <!-- Ignored at -->
            <td class="px-4 py-2 text-[var(--muted)] text-xs hidden lg:table-cell whitespace-nowrap">
              {{ formatDate(item.ignored_at) }}
            </td>
            <!-- Expires -->
            <td class="px-4 py-2 text-xs hidden xl:table-cell whitespace-nowrap">
              <span v-if="item.expire_at" :class="isExpiringSoon(item.expire_at) ? 'text-orange-400' : 'text-[var(--muted)]'">
                {{ formatDate(item.expire_at) }}
              </span>
              <span v-else class="text-[var(--muted)]">{{ t('ignored.permanent') }}</span>
            </td>
            <!-- Actions -->
            <td class="px-3 py-2">
              <div class="flex items-center gap-1.5">
                <button
                  :title="t('ignored.requeue')"
                  class="w-7 h-7 rounded flex items-center justify-center text-[var(--muted)] hover:text-[var(--accent)] hover:bg-[var(--accent)]/10 transition-colors"
                  @click="doRequeue(item)"
                ><i class="fas fa-undo text-xs" /></button>
                <button
                  :title="t('ignored.remove')"
                  class="w-7 h-7 rounded flex items-center justify-center text-[var(--muted)] hover:text-red-400 hover:bg-red-500/10 transition-colors"
                  @click="confirmRemove(item)"
                ><i class="fas fa-trash text-xs" /></button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Remove confirm modal -->
    <Teleport to="body">
      <div v-if="removeTarget" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" @mousedown.self="removeTarget = null">
        <div class="bg-[var(--bg1)] border border-[var(--border)] rounded-2xl p-6 w-full max-w-sm shadow-2xl space-y-4">
          <p class="text-sm">{{ t('ignored.confirmRemove') }} <strong>{{ removeTarget.title }}</strong></p>
          <div class="flex justify-end gap-3">
            <button class="px-4 py-2 text-sm text-[var(--muted)] hover:text-white" @click="removeTarget = null">{{ t('common.cancel') }}</button>
            <button class="px-4 py-2 bg-red-500 hover:bg-red-600 text-white text-sm rounded-lg transition-colors" @click="doRemove">{{ t('ignored.removeBtn') }}</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import api from '@/api/client'

const { t } = useI18n()

function typeLabel(type) {
  const map = {
    Movie: t('media.movie'),
    Series: t('media.series'),
    Episode: t('media.episode'),
    Season: t('media.season'),
  }
  return map[type] || type
}

const items        = ref([])
const loading      = ref(false)
const error        = ref('')
const search       = ref('')
const removeTarget = ref(null)

function isSeries(tp) { return tp === 'Episode' || tp === 'Series' || tp === 'Season' }

function formatDate(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric' })
}

function isExpiringSoon(expireAt) {
  if (!expireAt) return false
  const diff = new Date(expireAt) - new Date()
  return diff > 0 && diff < 7 * 86400000
}

async function load() {
  loading.value = true; error.value = ''
  try {
    const params = {}
    if (search.value) params.search = search.value
    const { data } = await api.get('/ignored', { params })
    items.value = data || []
  } catch { error.value = t('ignored.error.loadFailed') }
  finally { loading.value = false }
}

async function doRequeue(item) {
  await api.post(`/ignored/${item.id}/requeue`)
  load()
}

function confirmRemove(item) { removeTarget.value = item }
async function doRemove() {
  if (!removeTarget.value) return
  await api.delete(`/ignored/${removeTarget.value.id}`)
  removeTarget.value = null
  load()
}

let timer = null
watch(search, () => { clearTimeout(timer); timer = setTimeout(load, 300) })
onMounted(load)
</script>
