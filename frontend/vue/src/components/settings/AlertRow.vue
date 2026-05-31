<template>
  <div class="space-y-2">
    <div class="flex items-center justify-between py-2 px-3 bg-[var(--bg3)] rounded-lg">
      <span class="text-sm">{{ label }}</span>
      <ToggleSlider :model-value="isEnabled" @update:model-value="toggle" />
    </div>
    <div v-if="isEnabled" class="grid grid-cols-2 gap-2 px-1">
      <input
        type="text"
        :placeholder="mentionPlaceholder"
        :value="mention"
        class="field text-xs"
        @input="$emit('update:mention', $event.target.value)"
      />
      <input
        type="text"
        :placeholder="msgPlaceholder"
        :value="msg"
        class="field text-xs"
        @input="$emit('update:msg', $event.target.value)"
      />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import ToggleSlider from '@/components/ui/ToggleSlider.vue'

const props = defineProps({
  label:              { type: String, default: '' },
  enabled:            { type: String, default: 'false' },
  mention:            { type: String, default: '' },
  msg:                { type: String, default: '' },
  mentionPlaceholder: { type: String, default: 'Mention (@role / @user)' },
  msgPlaceholder:     { type: String, default: 'Message personnalisé' },
})

const emit = defineEmits(['update:enabled', 'update:mention', 'update:msg'])

const isEnabled = computed(() =>
  props.enabled === 'true' || props.enabled === true
)

function toggle(v) {
  emit('update:enabled', String(v))
}
</script>

<style scoped>
.field { @apply w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]; }
</style>
