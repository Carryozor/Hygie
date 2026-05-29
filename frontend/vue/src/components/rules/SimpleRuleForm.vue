<template>
  <div class="space-y-4">
    <div class="space-y-1">
      <label class="text-xs text-[var(--muted)] uppercase tracking-wide">Utilisateur Seerr</label>
      <select
        v-model="form.seerr_user_id"
        class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]"
        @change="onUserChange"
      >
        <option value="">— Choisir un utilisateur —</option>
        <option v-for="u in users" :key="u.id" :value="u.id">{{ u.username }}</option>
      </select>
    </div>

    <div class="space-y-1">
      <label class="text-xs text-[var(--muted)] uppercase tracking-wide">Bibliothèque</label>
      <select
        v-model="form.library_id"
        class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]"
      >
        <option value="">— Toutes les bibliothèques —</option>
        <option v-for="lib in libraries" :key="lib.id" :value="lib.id">{{ lib.name }}</option>
      </select>
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

const props = defineProps({
  initial: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:modelValue'])

const servers = useServersStore()
const users = ref([])

const form = reactive({
  seerr_user_id: props.initial.seerr_user_id ?? '',
  seerr_username: props.initial.seerr_username ?? '',
  library_id: props.initial.library_id ?? '',
  grace_days: props.initial.grace_days ?? 30,
  discord_id: props.initial.discord_id ?? '',
  enabled: props.initial.enabled !== false,
})

const libraries = computed(() => servers.libraries)

watch(form, () => emit('update:modelValue', { ...form }), { deep: true })

function onUserChange() {
  const u = users.value.find(u => u.id === form.seerr_user_id)
  form.seerr_username = u ? u.username : ''
}

onMounted(async () => {
  try {
    const { data } = await api.get('/seerr-rules/users')
    users.value = data || []
  } catch {}
  if (!servers.libraries.length) await servers.fetch()
})
</script>
