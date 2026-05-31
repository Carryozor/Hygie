<template>
  <div class="space-y-3">
    <div
      v-for="(srv, idx) in servers"
      :key="srv._key"
      class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-5 space-y-4"
    >
      <!-- Header -->
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-lg bg-[#FFBE00]/20 flex items-center justify-center shrink-0">
          <ServiceIcon name="radarr" :size="22" />
        </div>
        <input
          v-model="srv.name"
          type="text"
          placeholder="Nom de l'instance"
          class="flex-1 bg-transparent text-sm font-semibold focus:outline-none placeholder:text-[var(--muted)]"
          @input="sync"
        />
        <label class="flex items-center gap-2 text-xs text-[var(--muted)] cursor-pointer select-none">
          <input v-model="srv.enabled" type="checkbox" class="accent-[var(--accent)]" @change="sync" />
          Actif
        </label>
        <button
          type="button"
          class="text-[var(--muted)] hover:text-red-400 transition-colors"
          title="Supprimer"
          @click="removeServer(idx)"
        >
          <i class="fas fa-trash-can text-sm" />
        </button>
      </div>

      <!-- URL -->
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">URL</label>
        <input v-model="srv.url" type="url" placeholder="http://radarr:7878" class="field font-mono" @input="sync" />
      </div>

      <!-- API Key -->
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Clé API</label>
        <div class="flex gap-2">
          <input
            v-model="srv.api_key"
            :type="srv._showKey ? 'text' : 'password'"
            placeholder="••••••••"
            class="flex-1 field font-mono"
            @input="sync"
          />
          <button
            type="button"
            class="px-3 py-2 border border-[var(--border)] rounded-lg text-[var(--muted)] hover:text-white"
            @click="srv._showKey = !srv._showKey"
          >
            <i :class="['fas', srv._showKey ? 'fa-eye-slash' : 'fa-eye', 'text-sm']" />
          </button>
        </div>
      </div>

      <!-- Test -->
      <div class="flex items-center gap-2">
        <button
          type="button"
          class="text-xs px-3 py-1.5 rounded-lg border transition-colors whitespace-nowrap"
          :class="srv._state === 'ok'    ? 'border-green-500/50 text-green-400' :
                  srv._state === 'error' ? 'border-red-500/50 text-red-400' :
                  'border-[var(--border)] text-[var(--muted)] hover:text-white'"
          @click="testInstance(idx)"
        >
          {{ srv._state === 'loading' ? '…' : srv._state === 'ok' ? '✓ OK' : srv._state === 'error' ? '✗ Erreur' : 'Tester' }}
        </button>
        <span v-if="srv._msg" class="text-xs" :class="srv._state === 'ok' ? 'text-green-400' : 'text-red-400'">
          {{ srv._msg }}
        </span>
      </div>
    </div>

    <button
      type="button"
      class="w-full py-2.5 border border-dashed border-[var(--border)] rounded-xl text-sm text-[var(--muted)] hover:text-white hover:border-[var(--accent)] transition-colors"
      @click="addServer"
    >
      <i class="fas fa-plus mr-2" />Ajouter une instance Radarr
    </button>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, nextTick } from 'vue'
import api from '@/api/client'
import ServiceIcon from '@/components/ui/ServiceIcon.vue'

const props = defineProps({ form: { type: Object, required: true } })

let _keyCounter = 0
let _selfUpdate = false
const servers = ref([])

function _decorate(list) {
  return list.map(s => ({ ...s, _key: ++_keyCounter, _showKey: false, _state: 'idle', _msg: '' }))
}

function parseServers({ legacyFallback = false } = {}) {
  try {
    const raw = JSON.parse(props.form.radarr_servers || '[]')
    if (Array.isArray(raw) && raw.length) return _decorate(raw)
  } catch (_e) { /* JSON parse failure — use fallback */ }
  if (legacyFallback && props.form.radarr_url) {
    return _decorate([{
      id: 'legacy', name: 'Radarr', url: props.form.radarr_url,
      api_key: props.form.radarr_api_key || '', enabled: true,
    }])
  }
  return []
}

function sync() {
  _selfUpdate = true
  const clean = servers.value.map(({ _key, _showKey, _state, _msg, ...s }) => s)
  props.form.radarr_servers = JSON.stringify(clean)
  nextTick(() => { _selfUpdate = false })
}

function addServer() {
  servers.value.push({
    id: `radarr-${Date.now()}`, name: 'Radarr', url: '', api_key: '', enabled: true,
    _key: ++_keyCounter, _showKey: false, _state: 'idle', _msg: '',
  })
  sync()
}

function removeServer(idx) {
  servers.value.splice(idx, 1)
  if (servers.value.length === 0) {
    props.form.radarr_url = ''
    props.form.radarr_api_key = ''
  }
  sync()
}

async function testInstance(idx) {
  const srv = servers.value[idx]
  srv._state = 'loading'; srv._msg = ''
  try {
    const { data } = await api.post('/settings/test-arr', {
      type: 'radarr', url: srv.url, api_key: srv.api_key,
    })
    srv._state = data.ok ? 'ok' : 'error'
    srv._msg   = data.message || ''
  } catch {
    srv._state = 'error'; srv._msg = ''
  }
  setTimeout(() => { srv._state = 'idle'; srv._msg = '' }, 6000)
}

onMounted(() => { servers.value = parseServers({ legacyFallback: true }) })

watch(() => props.form.radarr_servers, () => {
  if (_selfUpdate) return
  const parsed = parseServers()
  const cleanParsed  = JSON.stringify(parsed.map(({ _key, _showKey, _state, _msg, ...s }) => s))
  const cleanCurrent = JSON.stringify(servers.value.map(({ _key, _showKey, _state, _msg, ...s }) => s))
  if (cleanParsed !== cleanCurrent) servers.value = parsed
})
</script>

<style scoped>
.field { @apply w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]; }
</style>
