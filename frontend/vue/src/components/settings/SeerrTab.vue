<template>
  <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-4">
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-lg bg-[#8B5CF6]/20 flex items-center justify-center">
          <ServiceIcon name="overseerr" :size="22" />
        </div>
        <h2 class="font-semibold">Overseerr / Jellyseerr</h2>
      </div>
      <TestBtn service="seerr" />
    </div>
    <div>
      <label class="block text-xs text-[var(--muted)] mb-1">URL</label>
      <input v-model="form.seerr_url" type="url" placeholder="http://seerr:5055" class="field font-mono" />
    </div>
    <div>
      <label class="block text-xs text-[var(--muted)] mb-1">Clé API</label>
      <div class="flex gap-2">
        <input v-model="form.seerr_api_key" :type="showKey ? 'text' : 'password'" placeholder="••••••••" class="flex-1 field font-mono" />
        <button type="button" class="px-3 py-2 border border-[var(--border)] rounded-lg text-[var(--muted)] hover:text-white" @click="showKey = !showKey">
          <i :class="['fas', showKey ? 'fa-eye-slash' : 'fa-eye', 'text-sm']" />
        </button>
      </div>
    </div>
    <div>
      <label class="block text-xs text-[var(--muted)] mb-1">URL externe (liens dans les notifications)</label>
      <input v-model="form.seerr_external_url" type="url" placeholder="https://seerr.mondomaine.fr" class="field font-mono" />
    </div>

    <!-- Sync Radarr/Sonarr -->
    <div class="border border-[var(--border)] rounded-lg p-4 space-y-3 bg-[var(--bg3)]/40">
      <div class="flex items-start gap-3">
        <i class="fas fa-link text-[var(--accent)] mt-0.5 text-sm" />
        <div class="flex-1 space-y-1">
          <div class="text-sm font-medium">Importer Radarr &amp; Sonarr depuis Seerr</div>
          <div class="text-xs text-[var(--muted)] leading-relaxed">
            Seerr connaît déjà vos instances Radarr et Sonarr (y compris Radarr 4K, Sonarr 4K…).
            Cliquez pour les importer automatiquement — les instances déjà configurées dans Hygie ne seront pas écrasées.
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
          <i :class="['fas', syncing ? 'fa-spinner fa-spin' : syncState === 'ok' ? 'fa-check' : syncState === 'error' ? 'fa-times' : 'fa-download', 'text-xs']" />
          {{ syncing ? 'Import en cours…' : syncState === 'ok' ? 'Importé !' : syncState === 'error' ? 'Échec' : 'Importer les instances' }}
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
          <div class="text-sm font-semibold">IDs Discord des utilisateurs</div>
          <div class="text-xs text-[var(--muted)]">Associez un ID Discord à chaque utilisateur Seerr pour les notifications personnalisées</div>
        </div>
        <button
:disabled="loadingUsers" class="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-[var(--bg3)] border border-[var(--border)] hover:bg-[var(--border)] transition-colors disabled:opacity-50"
          @click="loadUsers">
          <i :class="['fas', loadingUsers ? 'fa-spinner fa-spin' : 'fa-rotate', 'text-[10px]']" />
          {{ loadingUsers ? 'Chargement…' : 'Récupérer les utilisateurs' }}
        </button>
      </div>

      <div v-if="usersError" class="text-xs text-red-400 bg-red-500/10 rounded-lg px-3 py-2">{{ usersError }}</div>

      <div v-if="users.length" class="space-y-2">
        <div v-for="u in users" :key="u.id" class="flex items-center gap-3 py-2 px-3 bg-[var(--bg3)] rounded-lg">
          <div class="flex-1 min-w-0">
            <div class="text-sm font-medium truncate">{{ u.username }}</div>
            <div class="text-xs text-[var(--muted)]">ID Seerr : {{ u.id }}</div>
          </div>
          <div class="flex items-center gap-2 flex-shrink-0">
            <input
v-model="u._discord_id" type="text" placeholder="ID Discord (ex: 123456789012345678)"
              class="w-52 bg-[var(--bg2)] border border-[var(--border)] rounded-lg px-2 py-1.5 text-xs font-mono focus:outline-none focus:border-[var(--accent)]" />
            <button
:disabled="u._saving" class="text-xs px-2.5 py-1.5 rounded-lg transition-colors disabled:opacity-50 flex items-center gap-1"
              :class="u._saved ? 'bg-green-500/20 text-green-400 border border-green-500/30' : 'bg-[var(--accent)]/20 text-[var(--accent)] border border-[var(--accent)]/30 hover:bg-[var(--accent)]/30'"
              @click="saveDiscordId(u)">
              <i :class="['fas', u._saving ? 'fa-spinner fa-spin' : u._saved ? 'fa-check' : 'fa-save', 'text-[10px]']" />
              {{ u._saving ? '' : u._saved ? 'Sauvé' : 'Sauver' }}
            </button>
          </div>
        </div>
        <p class="text-xs text-[var(--muted)]">
          <i class="fas fa-info-circle mr-1" />Pour trouver un ID Discord : activez le mode développeur dans Discord, puis clic droit sur l'utilisateur → Copier l'identifiant.
        </p>
      </div>

      <div v-else-if="!loadingUsers && fetched" class="text-xs text-[var(--muted)] text-center py-3">
        Aucun utilisateur Seerr trouvé. Vérifiez la configuration.
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref } from 'vue'
import ServiceIcon from '@/components/ui/ServiceIcon.vue'
import TestBtn from '@/components/ui/TestBtn.vue'
import api from '@/api/client'

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
    // Update form with merged lists so the Radarr/Sonarr tabs reflect the new servers
    if (data.radarr_servers) props.form.radarr_servers = JSON.stringify(data.radarr_servers)
    if (data.sonarr_servers) props.form.sonarr_servers = JSON.stringify(data.sonarr_servers)
  } catch (e) {
    syncState.value = 'error'
    syncMsg.value   = e?.response?.data?.detail || 'Impossible de contacter Seerr'
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
    usersError.value = e?.response?.data?.detail || 'Impossible de contacter Seerr'
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
