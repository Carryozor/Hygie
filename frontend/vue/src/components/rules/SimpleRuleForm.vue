<template>
  <div class="space-y-4">
    <div class="space-y-1">
      <label class="text-xs text-[var(--muted)] uppercase tracking-wide">Nom de la règle</label>
      <input
        v-model="form.name"
        type="text" placeholder="Ex: Alice — Films seulement"
        class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]"
      />
    </div>

    <div class="space-y-1">
      <label class="text-xs text-[var(--muted)] uppercase tracking-wide">Utilisateur Seerr</label>
      <div v-if="loadingUsers" class="text-xs text-[var(--muted)] py-2 flex items-center gap-2">
        <i class="fas fa-spinner fa-spin text-[10px]" /> Chargement des utilisateurs…
      </div>
      <div v-else-if="users.length" class="space-y-1.5">
        <input
          v-model="userSearch"
          type="text"
          placeholder="Rechercher un utilisateur…"
          class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:border-[var(--accent)]"
        />
        <div class="max-h-40 overflow-y-auto space-y-0.5 rounded-lg border border-[var(--border)] bg-[var(--bg3)]">
          <button
            v-for="u in filteredUsers"
            :key="u.id"
            type="button"
            class="w-full flex items-center gap-3 px-3 py-2 text-sm text-left transition-colors hover:bg-[var(--bg2)]"
            :class="form.seerr_user_id === u.id ? 'bg-[var(--accent)]/10 text-[var(--accent)]' : 'text-[var(--text)]'"
            @click="selectUser(u)"
          >
            <i :class="['fas', form.seerr_user_id === u.id ? 'fa-circle-check' : 'fa-circle', 'text-xs flex-shrink-0', form.seerr_user_id === u.id ? 'text-[var(--accent)]' : 'text-[var(--border)]']" />
            <span class="flex-1 truncate">{{ u.username }}</span>
            <span v-if="u.discord_id" class="text-[10px] text-[var(--muted)] flex-shrink-0 flex items-center gap-1">
              <i class="fab fa-discord" /> Discord
            </span>
          </button>
          <div v-if="!filteredUsers.length" class="text-xs text-[var(--muted)] text-center py-3">Aucun résultat</div>
        </div>
        <div v-if="form.seerr_user_id" class="text-xs text-[var(--muted)]">
          Sélectionné : <span class="text-[var(--text)] font-medium">{{ form.seerr_username }}</span>
        </div>
      </div>
      <div v-else class="text-xs text-[var(--muted)] py-2">
        Aucun utilisateur Seerr trouvé. Vérifiez la configuration Seerr dans les paramètres.
      </div>
    </div>

    <div class="space-y-1">
      <label class="text-xs text-[var(--muted)] uppercase tracking-wide">
        Bibliothèques
        <span class="font-normal normal-case">
          ({{ form.library_ids === null ? 'toutes' : form.library_ids.length + ' sélectionnée(s)' }})
        </span>
      </label>
      <div class="bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-2 py-2 max-h-48 overflow-y-auto">
        <LibraryTreePicker v-model="form.library_ids" />
      </div>
    </div>

    <div class="space-y-1">
      <label class="text-xs text-[var(--muted)] uppercase tracking-wide">Délai de grâce (jours)</label>
      <input
        v-model.number="form.grace_days"
        type="number" min="0" max="3650"
        class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]"
      />
    </div>

    <div class="space-y-1">
      <label class="text-xs text-[var(--muted)] uppercase tracking-wide">
        Discord ID <span class="font-normal normal-case">(optionnel)</span>
      </label>
      <input
        v-model="form.discord_id"
        type="text" placeholder="ex: 123456789012345678"
        class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]"
      />
    </div>

    <label class="flex items-center gap-3 cursor-pointer select-none">
      <input v-model="form.enabled" type="checkbox" class="hidden" />
      <span :class="['w-9 h-5 rounded-full flex items-center transition-colors duration-200', form.enabled ? 'bg-[var(--accent)]' : 'bg-[var(--border)]']">
        <span :class="['w-4 h-4 bg-white rounded-full shadow transition-transform duration-200 mx-0.5', form.enabled ? 'translate-x-4' : 'translate-x-0']" />
      </span>
      <span class="text-sm">Règle active</span>
    </label>
  </div>
</template>

<script setup>
import { reactive, computed, watch, onMounted, ref } from 'vue'
import api from '@/api/client'
import { useServersStore } from '@/stores/servers'
import LibraryTreePicker from './LibraryTreePicker.vue'

const props = defineProps({
  initial: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:modelValue'])

const servers      = useServersStore()
const users        = ref([])
const loadingUsers = ref(false)
const userSearch   = ref('')

const form = reactive({
  name:           props.initial.name          ?? '',
  seerr_user_id:  props.initial.seerr_user_id ?? '',
  seerr_username: props.initial.seerr_username ?? '',
  library_ids:    props.initial.library_ids   ?? null,
  grace_days:     props.initial.grace_days    ?? 30,
  discord_id:     props.initial.discord_id    ?? '',
  enabled:        props.initial.enabled       !== false,
})

const filteredUsers = computed(() => {
  const q = userSearch.value.toLowerCase()
  return q ? users.value.filter(u => u.username.toLowerCase().includes(q)) : users.value
})

watch(form, () => emit('update:modelValue', { ...form }), { deep: true })

function selectUser(u) {
  form.seerr_user_id  = u.id
  form.seerr_username = u.username
  if (u.discord_id && !form.discord_id) form.discord_id = u.discord_id
}

onMounted(async () => {
  loadingUsers.value = true
  try {
    const { data } = await api.get('/seerr-rules/users')
    users.value = data || []
    if (props.initial.seerr_user_id && !users.value.length) {
      users.value = [{ id: props.initial.seerr_user_id, username: props.initial.seerr_username || `#${props.initial.seerr_user_id}`, discord_id: '' }]
    }
  } catch {
    if (props.initial.seerr_user_id) {
      users.value = [{ id: props.initial.seerr_user_id, username: props.initial.seerr_username || `#${props.initial.seerr_user_id}`, discord_id: '' }]
    }
  } finally {
    loadingUsers.value = false
  }
  if (!servers.libraries.length) await servers.fetch()
})
</script>
