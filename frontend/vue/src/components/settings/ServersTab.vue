<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <div class="text-sm text-[var(--muted)]">{{ t('settings.servers.description') }}</div>
      <button class="flex items-center gap-1.5 px-3 py-2 bg-[var(--accent)] hover:opacity-90 rounded-lg text-sm transition-opacity" @click="addServer">
        <i class="fas fa-plus text-xs" /> {{ t('settings.servers.add') }}
      </button>
    </div>

    <div v-if="!mediaServers.length" class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-8 text-center text-[var(--muted)] text-sm">
      <i class="fas fa-server text-3xl mb-3 block opacity-30" />
      {{ t('settings.servers.empty') }}
    </div>

    <div v-for="(srv, idx) in mediaServers" :key="srv._uid" class="bg-[var(--bg2)] border rounded-xl overflow-hidden" :class="serverBorderClass(srv.type)">
      <div class="flex items-center justify-between px-5 py-3 border-b border-[var(--border)]" :class="serverHeaderClass(srv.type)">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-lg flex items-center justify-center bg-black/20">
            <ServiceIcon v-if="serverService(srv.type)" :name="serverService(srv.type)" :size="22" />
            <i v-else class="fas fa-server text-[var(--muted)] text-sm" />
          </div>
          <div>
            <div class="text-sm font-semibold">{{ srv.name || t('settings.servers.unnamed') }}</div>
            <div class="text-xs opacity-70 capitalize">{{ srv.type || t('settings.servers.unknownType') }}</div>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <button
class="text-xs px-3 py-1.5 rounded-lg border transition-colors"
            :class="srv._testOk === true ? 'border-green-500/50 text-green-400' : srv._testOk === false ? 'border-red-500/50 text-red-400' : 'border-[var(--border)] text-[var(--muted)] hover:text-white'"
            @click="testServer(srv)">
            {{ srv._testing ? '…' : srv._testOk === true ? t('common.ok') : srv._testOk === false ? `✗ ${t('common.failed')}` : t('common.test') }}
          </button>
          <span v-if="srv._testMsg && !srv._testing" class="text-xs" :class="srv._testOk ? 'text-green-400' : 'text-red-400'">{{ srv._testMsg }}</span>
          <button class="text-[var(--muted)] hover:text-red-400 transition-colors" @click="removeServer(idx)">
            <i class="fas fa-trash text-sm" />
          </button>
        </div>
      </div>
      <div class="p-5 grid grid-cols-1 gap-4">
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-xs text-[var(--muted)] mb-1">{{ t('common.name') }}</label>
            <input v-model="srv.name" type="text" placeholder="Mon serveur" class="field" />
          </div>
          <div>
            <label class="block text-xs text-[var(--muted)] mb-1">{{ t('common.type') }}</label>
            <select v-model="srv.type" class="field">
              <option value="">{{ t('common.select') }}</option>
              <option value="emby">Emby</option>
              <option value="jellyfin">Jellyfin</option>
              <option value="plex">Plex</option>
            </select>
          </div>
        </div>
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.servers.localUrl') }}</label>
          <input v-model="srv.url" type="url" placeholder="http://192.168.1.10:8096" class="field font-mono" />
        </div>
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">{{ srv.type === 'plex' ? t('settings.servers.plexToken') : t('settings.servers.apiKey') }}</label>
          <div class="flex gap-2">
            <input v-model="srv.api_key" :type="srv._showKey ? 'text' : 'password'" placeholder="••••••••" class="flex-1 field font-mono" />
            <button type="button" class="px-3 py-2 border border-[var(--border)] rounded-lg text-[var(--muted)] hover:text-white" @click="srv._showKey = !srv._showKey">
              <i :class="['fas', srv._showKey ? 'fa-eye-slash' : 'fa-eye', 'text-sm']" />
            </button>
          </div>
          <div v-if="srv.type === 'plex'" class="mt-2 text-xs text-[var(--muted)] bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 space-y-1">
            <div class="font-semibold text-[var(--text)] flex items-center gap-1.5"><i class="fas fa-lightbulb text-yellow-400 text-[10px]" /> {{ t('settings.servers.howToGetToken') }}</div>
            <div><span class="font-medium">{{ t('settings.servers.linux') }}</span> <code class="bg-black/30 px-1 rounded text-[10px] break-all">grep -oP 'PlexOnlineToken="\K[^"]+' "VotreRepertoirePlex/Library/Application Support/Plex Media Server/Preferences.xml"</code></div>
            <div><span class="font-medium">{{ t('settings.servers.windows') }}</span> ouvrez <code class="bg-black/30 px-1 rounded text-[10px]">%LOCALAPPDATA%\Plex Media Server\Preferences.xml</code> et recherchez <code class="bg-black/30 px-1 rounded text-[10px]">PlexOnlineToken</code></div>
          </div>
        </div>
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.servers.externalUrl') }}</label>
          <input
v-model="srv.ext_url" type="url"
            :placeholder="srv.type === 'jellyfin' ? 'https://jellyfin.mondomaine.fr' : srv.type === 'plex' ? 'https://plex.mondomaine.fr' : 'https://emby.mondomaine.fr'"
            class="field font-mono" />
        </div>
        <div class="flex items-center justify-between">
          <div class="text-sm">{{ t('common.enabled') }}</div>
          <ToggleSlider v-model="srv.enabled" />
        </div>
      </div>

      <!-- Plex global settings + sections (Plex only) -->
      <div v-if="srv.type === 'plex'" class="px-5 pb-5 pt-4 border-t border-[var(--border)] space-y-4">
        <div class="space-y-3">
          <div class="text-xs font-semibold text-[var(--muted)] uppercase tracking-wide">{{ t('settings.servers.plexSettings') }}</div>
          <div>
            <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.servers.webhookSecret') }} <span class="font-normal">({{ t('settings.servers.plexPassOnly') }})</span></label>
            <div class="flex gap-2">
              <input v-model="props.form.plex_webhook_secret" :type="showWebhookSecret ? 'text' : 'password'" :placeholder="t('settings.servers.secretOptional')" class="flex-1 field font-mono" />
              <button type="button" class="px-3 py-2 border border-[var(--border)] rounded-lg text-[var(--muted)] hover:text-white" @click="showWebhookSecret = !showWebhookSecret">
                <i :class="['fas', showWebhookSecret ? 'fa-eye-slash' : 'fa-eye', 'text-sm']" />
              </button>
            </div>
            <p class="text-xs text-[var(--muted)] mt-1">{{ t('settings.servers.webhookUrl') }} <code class="bg-[var(--bg3)] px-1 rounded">{{ webhookUrl }}</code></p>
          </div>
          <div class="flex items-center justify-between">
            <div>
              <div class="text-sm font-medium">{{ t('settings.servers.plexOverlay') }}</div>
              <div class="text-xs text-[var(--muted)]">{{ t('settings.servers.overlayDesc') }}</div>
            </div>
            <ToggleSlider v-model="props.form.plex_overlay_enabled" />
          </div>
        </div>
      </div>

      <!-- Sections Plex discovery (Plex only, after save) -->
      <div v-if="srv.type === 'plex' && srv.id" class="px-5 pb-5 pt-4 border-t border-[var(--border)] space-y-3">
        <div class="flex items-center justify-between">
          <div class="text-xs font-semibold text-[var(--muted)] uppercase tracking-wide">{{ t('settings.servers.plexSections') }}</div>
          <button
:disabled="plexState[srv._uid]?.loading" class="text-xs px-3 py-1.5 rounded-lg bg-[var(--bg3)] border border-[var(--border)] hover:bg-[var(--border)] transition-colors disabled:opacity-50 flex items-center gap-1.5"
            @click="discoverPlex(srv)">
            <i :class="['fas', plexState[srv._uid]?.loading ? 'fa-spinner fa-spin' : 'fa-rotate', 'text-[10px]']" />
            {{ plexState[srv._uid]?.loading ? t('common.loading') : t('common.discover') }}
          </button>
        </div>
        <div v-if="plexState[srv._uid]?.error" class="text-xs text-red-400 bg-red-500/10 rounded-lg px-3 py-2">
          {{ plexState[srv._uid].error }}
        </div>
        <div v-else-if="plexState[srv._uid]?.sections?.length" class="space-y-2">
          <div
v-for="sec in plexState[srv._uid].sections" :key="sec.id"
            class="flex items-center justify-between py-2 px-3 bg-[var(--bg3)] rounded-lg">
            <div>
              <div class="text-sm font-medium">{{ sec.title }}</div>
              <div class="text-xs text-[var(--muted)] capitalize">{{ sec.type === 'movie' ? t('media.movies') : sec.type === 'show' ? t('media.series') : sec.type }}</div>
            </div>
            <span v-if="sec.configured" class="text-xs text-green-400 bg-green-500/10 border border-green-500/20 px-2 py-0.5 rounded-full">{{ t('settings.servers.added') }}</span>
            <button
v-else class="text-xs px-3 py-1 rounded-lg bg-[var(--accent)] hover:opacity-80 transition-opacity"
              @click="addPlexSection(srv, sec)">
              {{ t('settings.servers.addSection') }}
            </button>
          </div>
        </div>
        <div
v-else-if="plexState[srv._uid] && !plexState[srv._uid].loading"
          class="text-xs text-[var(--muted)] text-center py-3">
          {{ t('settings.servers.discoverFirst') }}
        </div>
        <div v-else class="text-xs text-[var(--muted)] text-center py-3">
          {{ t('settings.servers.saveThenDiscover') }}
        </div>
      </div>

      <!-- Bientôt supprimé (Emby / Jellyfin only) -->
      <div v-if="srv.type === 'emby' || srv.type === 'jellyfin'" class="px-5 pb-5 pt-4 border-t border-[var(--border)] space-y-3">
        <div class="text-xs font-semibold text-[var(--muted)] uppercase tracking-wide">{{ t('settings.servers.leavingSoonCollection') }}</div>
        <div class="flex items-center justify-between">
          <div>
            <div class="text-sm font-medium">{{ t('settings.servers.leavingSoonOverlay') }}</div>
            <div class="text-xs text-[var(--muted)]">{{ t('settings.servers.bannerDesc') }}</div>
          </div>
          <ToggleSlider v-model="form.emby_leaving_soon_overlay" />
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.servers.collectionName') }}</label>
            <input v-model="form.emby_leaving_soon_collection" type="text" :placeholder="t('settings.servers.leavingSoonDefault')" class="field" />
          </div>
          <div>
            <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.servers.thresholdDays') }}</label>
            <input v-model.number="form.emby_leaving_soon_days" type="number" min="1" class="field" />
          </div>
        </div>
      </div>
    </div>

    <button
:disabled="savingServers" class="w-full bg-[var(--bg3)] hover:bg-[var(--border)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm transition-colors disabled:opacity-50"
      @click="saveServers">
      {{ savingServers ? t('common.saving') : t('settings.servers.save') }}
    </button>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import ToggleSlider from '@/components/ui/ToggleSlider.vue'
import ServiceIcon from '@/components/ui/ServiceIcon.vue'
import { useSettingsStore } from '@/stores/settings'
import { useServersStore } from '@/stores/servers'
import { useStatusStore } from '@/stores/status'
import api from '@/api/client'

const { t } = useI18n()
const props = defineProps({ form: { type: Object, required: true } })

const settings          = useSettingsStore()
const servers           = useServersStore()
const statusStore       = useStatusStore()
const showWebhookSecret = ref(false)
const mediaServers      = ref([])
const savingServers     = ref(false)
const plexState         = ref({}) // _uid → { loading, sections, error }
let   _uid = 0

const webhookUrl = computed(() => {
  const secret = props.form.plex_webhook_secret
  return secret
    ? `${window.location.origin}/api/plex/webhook?secret=${secret}`
    : `${window.location.origin}/api/plex/webhook`
})

const SERVER_CONFIG = {
  emby:     { service: 'emby',     border: 'border-green-600/30',  header: 'bg-green-600/10' },
  jellyfin: { service: 'jellyfin', border: 'border-blue-500/30',   header: 'bg-blue-500/10' },
  plex:     { service: 'plex',     border: 'border-yellow-500/30', header: 'bg-yellow-500/10' },
}
const DEF = { service: null, border: 'border-[var(--border)]', header: '' }

function serverService(type)     { return (SERVER_CONFIG[type] || DEF).service }
function serverBorderClass(type) { return (SERVER_CONFIG[type] || DEF).border }
function serverHeaderClass(type) { return (SERVER_CONFIG[type] || DEF).header }

function addServer() {
  mediaServers.value.push({ _uid: ++_uid, id: null, name: '', url: '', api_key: '', ext_url: '', type: '', enabled: true, _showKey: true, _testing: false, _testOk: null, _testMsg: '' })
}
function removeServer(idx) { mediaServers.value.splice(idx, 1) }

async function testServer(srv) {
  if (!srv.id) return
  srv._testing = true; srv._testOk = null; srv._testMsg = ''
  try {
    const { data } = await api.post(`/settings/media-servers/${srv.id}/test`)
    srv._testOk  = data.ok
    srv._testMsg = data.message || ''
    if (data.server_type) srv.type = data.server_type
    await statusStore.checkServerHealth()
  } catch { srv._testOk = false; srv._testMsg = '' }
  finally { srv._testing = false }
}

const _detectTimers = new Map()
function scheduleAutoDetect(srv) {
  if (!srv.id) return
  if (_detectTimers.has(srv._uid)) clearTimeout(_detectTimers.get(srv._uid))
  _detectTimers.set(srv._uid, setTimeout(async () => {
    if (!srv.url) return
    try {
      const { data } = await api.post(`/settings/media-servers/${srv.id}/test`)
      if (data.server_type && data.server_type !== 'unknown') srv.type = data.server_type
    } catch { /* silent */ }
  }, 800))
}

watch(
  () => mediaServers.value.map(s => s.url),
  (urls, prev) => {
    if (!prev) return
    urls.forEach((url, i) => { if (url !== prev[i]) scheduleAutoDetect(mediaServers.value[i]) })
  }
)

async function discoverPlex(srv) {
  plexState.value[srv._uid] = { loading: true, sections: [], error: null }
  try {
    const { data } = await api.get(`/libraries/plex/${srv.id}/sections`)
    plexState.value[srv._uid] = { loading: false, sections: data, error: null }
  } catch (e) {
    const msg = e?.response?.data?.detail || t('settings.servers.error.plex')
    plexState.value[srv._uid] = { loading: false, sections: [], error: msg }
  }
}

async function addPlexSection(srv, section) {
  try {
    await api.post('/libraries', {
      name: section.title,
      emby_library_id: section.id,
      server_id: String(srv.id),
      enabled: true,
    })
    section.configured = true
    await servers.fetch()
  } catch { /* silent */ }
}

async function loadServers() {
  try {
    const { data } = await api.get('/settings/media-servers')
    mediaServers.value = (data || []).map(s => ({ ...s, _uid: ++_uid, _showKey: false, _testing: false, _testOk: null, _testMsg: '' }))
  } catch { /* silent */ }
}

async function saveServers() {
  savingServers.value = true
  try {
    const current    = (await api.get('/settings/media-servers')).data || []
    const currentIds = new Set(current.map(s => String(s.id)))
    for (const srv of mediaServers.value) {
      const { _uid, _showKey, _testing, _testOk, ...payload } = srv
      if (!payload.id) {
        const { data } = await api.post('/settings/media-servers', payload)
        srv.id = data.id
      } else {
        await api.put(`/settings/media-servers/${payload.id}`, payload)
        currentIds.delete(String(payload.id))
      }
    }
    for (const id of currentIds) await api.delete(`/settings/media-servers/${id}`)
    await loadServers()
    const f = props.form
    await settings.save({
      emby_leaving_soon_overlay:    String(f.emby_leaving_soon_overlay),
      emby_leaving_soon_collection: f.emby_leaving_soon_collection,
      emby_leaving_soon_days:       String(f.emby_leaving_soon_days),
      plex_webhook_secret:          f.plex_webhook_secret,
      plex_overlay_enabled:         String(f.plex_overlay_enabled),
    })
  } finally { savingServers.value = false }
}

onMounted(loadServers)
</script>

<style scoped>
.field {
  @apply w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)];
}
</style>
