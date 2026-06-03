<template>
  <div class="flex items-center gap-2">
    <button
      class="text-xs px-3 py-1.5 rounded-lg border transition-colors whitespace-nowrap"
      :class="state === 'ok'    ? 'border-green-500/50 text-green-400' :
              state === 'error' ? 'border-red-500/50 text-red-400' :
              'border-[var(--border)] text-[var(--muted)] hover:text-white'"
      @click="test"
    >
      {{ state === 'loading' ? '…' : state === 'ok' ? '✓ OK' : state === 'error' ? '✗ Erreur' : 'Tester' }}
    </button>
    <span v-if="msg" class="text-xs" :class="state === 'ok' ? 'text-green-400' : 'text-red-400'">{{ msg }}</span>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import api from '@/api/client'

const props = defineProps({ service: { type: String, required: true } })
const state = ref('idle')
const msg   = ref('')

async function test() {
  state.value = 'loading'
  msg.value   = ''
  try {
    const { data } = await api.post(`/settings/test/${props.service}`)
    state.value = data.ok ? 'ok' : 'error'
    msg.value   = data.message || ''
  } catch {
    state.value = 'error'
    msg.value   = ''
  }
  setTimeout(() => { state.value = 'idle'; msg.value = '' }, 6000)
}
</script>
