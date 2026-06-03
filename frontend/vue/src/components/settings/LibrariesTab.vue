<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <div class="text-sm text-[var(--muted)]">{{ t('settings.libraries.description') }}</div>
      <button
        class="flex items-center gap-1.5 px-3 py-2 bg-[var(--accent)] hover:opacity-90 rounded-lg text-sm transition-opacity"
        @click="openCreate">
        <i class="fas fa-plus text-xs" /> {{ t('settings.libraries.add') }}
      </button>
    </div>

    <div v-if="!libraries.length" class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-8 text-center text-[var(--muted)] text-sm">
      <i class="fas fa-layer-group text-3xl mb-3 block opacity-30" />
      {{ t('settings.libraries.empty') }}
    </div>

    <div
      v-for="lib in libraries" :key="lib.id"
      class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden">
      <div class="flex items-center gap-3 px-5 py-3 border-b border-[var(--border)] bg-[var(--bg3)]/30">
        <i class="fas fa-layer-group text-[var(--accent)] text-sm" />
        <div class="flex-1 min-w-0">
          <div class="font-semibold text-sm truncate">{{ lib.name }}</div>
          <div class="text-xs text-[var(--muted)] truncate">
            {{ serverName(lib.server_id) }} · {{ lib.emby_library_id }}
            · {{ lib.deletion_unit }} · {{ t('settings.libraries.graceDaysBadge', { n: lib.grace_days }) }}
          </div>
        </div>
        <div class="flex items-center gap-2">
          <span
            class="text-xs px-2 py-0.5 rounded-full"
            :class="lib.enabled ? 'bg-green-500/20 text-green-400' : 'bg-[var(--border)] text-[var(--muted)]'">
            {{ lib.enabled ? t('settings.libraries.active') : t('settings.libraries.inactive') }}
          </span>
          <button
            :title="lib._scanning ? t('settings.libraries.scanRunning') : t('settings.libraries.scanTrigger')"
            class="w-7 h-7 rounded flex items-center justify-center text-[var(--muted)] hover:text-[var(--accent)] hover:bg-[var(--accent)]/10 transition-colors"
            @click="triggerScan(lib)">
            <i :class="['fas', lib._scanning ? 'fa-spinner fa-spin' : 'fa-play', 'text-xs']" />
          </button>
          <button
            :title="t('common.edit')"
            class="w-7 h-7 rounded flex items-center justify-center text-[var(--muted)] hover:text-white hover:bg-[var(--bg3)] transition-colors"
            @click="openEdit(lib)">
            <i class="fas fa-pen text-xs" />
          </button>
          <button
            :title="t('common.delete')"
            class="w-7 h-7 rounded flex items-center justify-center text-[var(--muted)] hover:text-red-400 hover:bg-red-500/10 transition-colors"
            @click="askDelete(lib)">
            <i class="fas fa-trash text-xs" />
          </button>
        </div>
      </div>
      <div class="px-5 py-2 text-xs text-[var(--muted)] flex flex-wrap gap-3">
        <span v-if="lib.conditions?.length">
          <i class="fas fa-filter opacity-60 mr-1" />{{ lib.conditions.length }} {{ t('settings.libraries.conditions') }}
        </span>
        <span v-if="lib.seerr_conditions?.length">
          <i class="fas fa-user-tag opacity-60 mr-1" />{{ lib.seerr_conditions.length }} {{ t('settings.libraries.seerrRules') }}
        </span>
        <span v-else-if="!lib.conditions?.length" class="italic">{{ t('settings.libraries.noConditions') }}</span>
      </div>
    </div>

    <!-- Create / Edit modal -->
    <Teleport to="body">
      <div
        v-if="modal.open"
        class="fixed inset-0 z-50 flex items-start justify-center bg-black/60 backdrop-blur-sm p-4 pt-16 overflow-y-auto"
        @mousedown.self="modal.open = false">
        <div class="bg-[var(--bg1)] border border-[var(--border)] rounded-2xl w-full max-w-md shadow-2xl">
          <div class="flex items-center justify-between px-6 py-4 border-b border-[var(--border)]">
            <h3 class="font-semibold">{{ modal.id ? t('settings.libraries.modalEdit') : t('settings.libraries.modalCreate') }}</h3>
            <button class="text-[var(--muted)] hover:text-white" @click="modal.open = false">
              <i class="fas fa-xmark" />
            </button>
          </div>
          <div class="p-6 space-y-4">
            <div>
              <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.libraries.fieldServer') }}</label>
              <select v-model="modal.server_id" class="field" @change="loadEmbyLibraries">
                <option v-for="s in allServers" :key="s.id" :value="String(s.id)">
                  {{ s.name || t('settings.libraries.fieldServer') + ' ' + s.id }} ({{ s.type || '?' }})
                </option>
              </select>
            </div>
            <div>
              <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.libraries.fieldLibrary') }}</label>
              <select v-if="embyLibs.length" v-model="modal.emby_library_id" class="field">
                <option value="">{{ t('settings.libraries.choose') }}</option>
                <option v-for="el in embyLibs" :key="el.id" :value="el.id">{{ el.name }} ({{ el.id }})</option>
              </select>
              <input
                v-else v-model="modal.emby_library_id" type="text"
                :placeholder="t('settings.libraries.libIdPlaceholder')"
                class="field font-mono" />
              <div v-if="embyLibError" class="text-xs text-red-400 mt-1">
                <i class="fas fa-triangle-exclamation mr-1" />{{ embyLibError }}
              </div>
            </div>
            <div>
              <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.libraries.fieldName') }}</label>
              <input v-model="modal.name" type="text" :placeholder="t('settings.libraries.namePlaceholder')" class="field" />
            </div>
            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.libraries.fieldGraceDays') }}</label>
                <input v-model.number="modal.grace_days" type="number" min="0" max="3650" class="field" />
              </div>
              <div>
                <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.libraries.fieldDeletionUnit') }}</label>
                <select v-model="modal.deletion_unit" class="field">
                  <option value="episode">{{ t('settings.libraries.unitEpisode') }}</option>
                  <option value="season">{{ t('settings.libraries.unitSeason') }}</option>
                  <option value="series">{{ t('settings.libraries.unitSeries') }}</option>
                </select>
              </div>
            </div>
            <div class="flex items-center justify-between">
              <div class="text-sm">{{ t('settings.libraries.fieldEnabled') }}</div>
              <button
                type="button" :class="['w-9 h-5 rounded-full flex items-center transition-colors duration-200', modal.enabled ? 'bg-[var(--accent)]' : 'bg-[var(--border)]']"
                @click="modal.enabled = !modal.enabled">
                <span :class="['w-4 h-4 bg-white rounded-full shadow transition-transform duration-200 mx-0.5', modal.enabled ? 'translate-x-4' : 'translate-x-0']" />
              </button>
            </div>
            <div v-if="modal.error" class="text-xs text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2">
              {{ modal.error }}
            </div>
          </div>
          <div class="flex justify-end gap-3 px-6 pb-6">
            <button class="px-4 py-2 text-sm text-[var(--muted)] hover:text-white transition-colors" @click="modal.open = false">
              {{ t('common.cancel') }}
            </button>
            <button
              :disabled="modal.saving" class="px-4 py-2 bg-[var(--accent)] hover:opacity-90 disabled:opacity-50 text-white text-sm rounded-lg transition-opacity"
              @click="save">
              {{ modal.saving ? t('settings.libraries.saving') : (modal.id ? t('common.edit') : t('settings.libraries.modalCreate')) }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Delete confirm -->
    <ConfirmModal
      :show="!!deleteTarget"
      :message="deleteTarget ? t('settings.libraries.deleteConfirm', { name: deleteTarget.name }) : ''"
      :confirm-label="t('common.delete')"
      @confirm="doDelete"
      @cancel="deleteTarget = null"
    />

    <div v-if="scanMsg" class="bg-green-500/10 border border-green-500/30 text-green-400 rounded-lg px-4 py-3 text-sm">
      <i class="fas fa-circle-check mr-1" />{{ scanMsg }}
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useServersStore } from '@/stores/servers'
import ConfirmModal from '@/components/ui/ConfirmModal.vue'
import api from '@/api/client'

const { t } = useI18n()
const servers = useServersStore()

const libraries    = ref([])
const embyLibs     = ref([])
const embyLibError = ref('')
const deleteTarget = ref(null)
const scanMsg      = ref('')
const allServers   = ref([])

const modal = reactive({
  open: false, id: null, name: '', server_id: '0',
  emby_library_id: '', grace_days: 7, deletion_unit: 'episode',
  enabled: true, saving: false, error: '',
})

async function loadLibraries() {
  try {
    const { data } = await api.get('/libraries')
    libraries.value = (data || []).map(l => ({ ...l, _scanning: false }))
  } catch { /* silent */ }
}

async function loadEmbyLibraries() {
  embyLibError.value = ''
  embyLibs.value = []
  try {
    const { data } = await api.get('/libraries/emby', { params: { server_id: modal.server_id } })
    embyLibs.value = data || []
  } catch {
    embyLibError.value = t('settings.libraries.loadError')
  }
}

function serverName(serverId) {
  const s = allServers.value.find(s => String(s.id) === String(serverId))
  return s ? (s.name || `${t('settings.libraries.fieldServer')} ${s.id}`) : `${t('settings.libraries.fieldServer')} ${serverId || '0'}`
}

function openCreate() {
  Object.assign(modal, { open: true, id: null, name: '', server_id: String(allServers.value[0]?.id ?? '0'),
    emby_library_id: '', grace_days: 7, deletion_unit: 'episode', enabled: true, saving: false, error: '' })
  loadEmbyLibraries()
}

function openEdit(lib) {
  Object.assign(modal, { open: true, id: lib.id, name: lib.name, server_id: String(lib.server_id || '0'),
    emby_library_id: lib.emby_library_id, grace_days: lib.grace_days ?? 7,
    deletion_unit: lib.deletion_unit || 'episode', enabled: lib.enabled, saving: false, error: '' })
  loadEmbyLibraries()
}

async function save() {
  if (!modal.name.trim()) { modal.error = t('settings.libraries.nameRequired'); return }
  if (!modal.emby_library_id.trim()) { modal.error = t('settings.libraries.idRequired'); return }
  modal.saving = true; modal.error = ''
  try {
    const payload = {
      name: modal.name.trim(),
      server_id: modal.server_id,
      emby_library_id: modal.emby_library_id,
      grace_days: modal.grace_days,
      deletion_unit: modal.deletion_unit,
      enabled: modal.enabled,
    }
    if (modal.id) {
      await api.put(`/libraries/${modal.id}`, payload)
    } else {
      await api.post('/libraries', payload)
    }
    modal.open = false
    await loadLibraries()
    await servers.fetch()
  } catch (e) {
    modal.error = e?.response?.data?.detail || t('settings.libraries.saveError')
  } finally { modal.saving = false }
}

function askDelete(lib) { deleteTarget.value = lib }
async function doDelete() {
  if (!deleteTarget.value) return
  try {
    await api.delete(`/libraries/${deleteTarget.value.id}`)
    deleteTarget.value = null
    await loadLibraries()
    await servers.fetch()
  } catch { /* silent */ }
}

async function triggerScan(lib) {
  if (lib._scanning) return
  lib._scanning = true
  try {
    await api.post(`/libraries/${lib.id}/scan`)
    scanMsg.value = t('settings.libraries.scanLaunched', { name: lib.name })
    setTimeout(() => { scanMsg.value = '' }, 4000)
  } catch { /* silent */ }
  finally { lib._scanning = false }
}

onMounted(async () => {
  if (!servers.servers.length) await servers.fetch()
  allServers.value = servers.servers
  await loadLibraries()
})
</script>

<style scoped>
.field {
  @apply w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)];
}
</style>
