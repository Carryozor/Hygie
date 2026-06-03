<template>
  <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-4">
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-lg bg-[#8B5CF6]/20 flex items-center justify-center">
          <ServiceIcon name="overseerr" :size="22" />
        </div>
        <h2 class="font-semibold">{{ t('settings.seerr.title') }}</h2>
      </div>
      <TestBtn service="seerr" />
    </div>
    <div>
      <label class="block text-xs text-[var(--muted)] mb-1">{{ t('common.url') }}</label>
      <input v-model="form.seerr_url" type="url" :placeholder="t('settings.seerr.urlPlaceholder')" class="field font-mono" />
    </div>
    <div>
      <label class="block text-xs text-[var(--muted)] mb-1">{{ t('common.apiKey') }}</label>
      <div class="flex gap-2">
        <input v-model="form.seerr_api_key" :type="showKey ? 'text' : 'password'" placeholder="••••••••" class="flex-1 field font-mono" />
        <button type="button" class="px-3 py-2 border border-[var(--border)] rounded-lg text-[var(--muted)] hover:text-white" @click="showKey = !showKey">
          <i :class="['fas', showKey ? 'fa-eye-slash' : 'fa-eye', 'text-sm']" />
        </button>
      </div>
    </div>
    <div>
      <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.seerr.externalUrl') }}</label>
      <input v-model="form.seerr_external_url" type="url" :placeholder="t('settings.seerr.externalUrlPlaceholder')" class="field font-mono" />
    </div>

    <!-- Sync Radarr/Sonarr -->
    <div class="border border-[var(--border)] rounded-lg p-4 space-y-3 bg-[var(--bg3)]/40">
      <div class="flex items-start gap-3">
        <i class="fas fa-link text-[var(--accent)] mt-0.5 text-sm" />
        <div class="flex-1 space-y-1">
          <div class="text-sm font-medium">{{ t('settings.seerr.importRadarrSonarr') }}</div>
          <div class="text-xs text-[var(--muted)] leading-relaxed">
            {{ t('settings.seerr.importDescription') }}
          </div>
        </div>
      </div>
      <div class="flex items-center gap-3">
        <button
          type="button"
          :disabled="syncing || !form.seerr_url || !form.seerr_api_key"
          class="flex items-center gap-2 text-xs px-3 py-2 rounded-lg border transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          :class="syncState === 'ok'    ? 'border-green-500/50 text-green-400' :
                  syncState === 'error' ? 'border-red-500/50 text-red-400' :
                  'border-[var(--accent)]/50 text-[var(--accent)] hover:bg-[var(--accent)]/10'"
          @click="syncFromSeerr"
        >
          <i :class="['fas', syncing ? 'fa-spinner fa-spin' : syncState === 'ok' ? 'fa-check' : syncState === 'error' ? 'fa-xmark' : 'fa-download', 'text-xs']" />
          {{ syncing ? t('settings.seerr.importing') : syncState === 'ok' ? t('settings.seerr.imported') : syncState === 'error' ? t('common.failed') : t('settings.seerr.import') }}
        </button>
        <span v-if="syncMsg" class="text-xs" :class="syncState === 'ok' ? 'text-green-400' : 'text-red-400'">
          {{ syncMsg }}
        </span>
      </div>
    </div>

    <!-- Discord ID mappings -->
    <div class="border-t border-[var(--border)] pt-4 space-y-3">
      <div class="flex items-center justify-between">
        <div>
          <div class="text-sm font-semibold">{{ t('settings.seerr.discordIds.title') }}</div>
          <div class="text-xs text-[var(--muted)]">{{ t('settings.seerr.discordIds.description') }}</div>
        </div>
        <button
:disabled="loadingUsers" class="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-[var(--bg3)] border border-[var(--border)] hover:bg-[var(--border)] transition-colors disabled:opacity-50"
          @click="loadUsers">
          <i :class="['fas', loadingUsers ? 'fa-spinner fa-spin' : 'fa-rotate', 'text-[10px]']" />
          {{ loadingUsers ? t('common.loading') : t('settings.seerr.loadUsers') }}
        </button>
      </div>

      <div v-if="usersError" class="text-xs text-red-400 bg-red-500/10 rounded-lg px-3 py-2">{{ usersError }}</div>

      <div v-if="users.length" class="space-y-2">
        <div v-for="u in users" :key="u.id" class="flex items-center gap-3 py-2 px-3 bg-[var(--bg3)] rounded-lg">
          <div class="flex-1 min-w-0">
            <div class="text-sm font-medium truncate">{{ u.username }}</div>
            <div class="text-xs text-[var(--muted)]">{{ t('settings.seerr.seerrId') }} {{ u.id }}</div>
          </div>
          <div class="flex items-center gap-2 flex-shrink-0">
            <input
v-model="u._discord_id" type="text" :placeholder="t('settings.seerr.discordIdPlaceholder')"
              class="w-52 bg-[var(--bg2)] border border-[var(--border)] rounded-lg px-2 py-1.5 text-xs font-mono focus:outline-none focus:border-[var(--accent)]" />
            <button
:disabled="u._saving" class="text-xs px-2.5 py-1.5 rounded-lg transition-colors disabled:opacity-50 flex items-center gap-1"
              :class="u._saved ? 'bg-green-500/20 text-green-400 border border-green-500/30' : 'bg-[var(--accent)]/20 text-[var(--accent)] border border-[var(--accent)]/30 hover:bg-[var(--accent)]/30'"
              @click="saveDiscordId(u)">
              <i :class="['fas', u._saving ? 'fa-spinner fa-spin' : u._saved ? 'fa-check' : 'fa-save', 'text-[10px]']" />
              {{ u._saving ? '' : u._saved ? t('common.saved') : t('common.save') }}
            </button>
          </div>
        </div>
        <p class="text-xs text-[var(--muted)]">
          <i class="fas fa-circle-info mr-1" />{{ t('settings.seerr.howToFindDiscordId') }}
        </p>
      </div>

      <div v-else-if="!loadingUsers && fetched" class="text-xs text-[var(--muted)] text-center py-3">
        {{ t('settings.seerr.noUsers') }}
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import ServiceIcon from '@/components/ui/ServiceIcon.vue'
import TestBtn from '@/components/ui/TestBtn.vue'
import api from '@/api/client'

const { t } = useI18n()
const props = defineProps({ form: { type: Object, required: true } })

const showKey      = ref(false)
const users        = ref([])
const loadingUsers = ref(false)
const usersError   = ref('')
const fetched      = ref(false)
const syncing      = ref(false)
const syncState    = ref('idle')
const syncMsg      = ref('')

async function syncFromSeerr() {
  syncing.value   = true
  syncState.value = 'idle'
  syncMsg.value   = ''
  try {
    const { data } = await api.post('/settings/sync-arr-from-seerr', {
      seerr_url:     props.form.seerr_url,
      seerr_api_key: props.form.seerr_api_key,
    })
    syncState.value = 'ok'
    syncMsg.value   = data.message || ''
    if (data.radarr_servers) props.form.radarr_servers = JSON.stringify(data.radarr_servers)
    if (data.sonarr_servers) props.form.sonarr_servers = JSON.stringify(data.sonarr_servers)
  } catch (e) {
    syncState.value = 'error'
    syncMsg.value   = e?.response?.data?.detail || t('settings.seerr.error.contactFailed')
  } finally {
    syncing.value = false
    setTimeout(() => { syncState.value = 'idle'; syncMsg.value = '' }, 6000)
  }
}

async function loadUsers() {
  loadingUsers.value = true
  usersError.value = ''
  try {
    const { data } = await api.get('/seerr-rules/users')
    users.value = (data || []).map(u => ({
      ...u,
      _discord_id: u.discord_id || '',
      _saving: false,
      _saved: false,
    }))
    fetched.value = true
  } catch (e) {
    usersError.value = e?.response?.data?.detail || t('settings.seerr.error.contactFailed')
    fetched.value = true
  } finally {
    loadingUsers.value = false
  }
}

async function saveDiscordId(u) {
  u._saving = true
  u._saved = false
  try {
    await api.post('/seerr-rules/discord-mappings', {
      seerr_user_id: u.id,
      seerr_username: u.username,
      discord_id: u._discord_id.trim(),
    })
    u.discord_id = u._discord_id.trim()
    u._saved = true
    setTimeout(() => { u._saved = false }, 2000)
  } catch { /* silent */ } finally {
    u._saving = false
  }
}
</script>

<style scoped>
.field { @apply w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]; }
</style>
