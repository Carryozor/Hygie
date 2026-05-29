<template>
  <div class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-5 flex flex-col gap-2">
    <div class="flex items-center justify-between">
      <span class="text-xs text-[var(--muted)] uppercase tracking-wide">{{ label }}</span>
      <i :class="['fas', icon, 'text-[var(--accent)] opacity-70']" />
    </div>
    <div class="text-3xl font-bold">{{ displayValue }}</div>
    <div v-if="sub" class="text-xs text-[var(--muted)]">{{ sub }}</div>
  </div>
</template>
<script setup>
import { computed } from 'vue'
const props = defineProps({
  label:  { type: String, required: true },
  value:  { type: [Number, String], default: 0 },
  icon:   { type: String, default: 'fa-chart-bar' },
  sub:    { type: String, default: '' },
  format: { type: String, default: 'number' },
})
const displayValue = computed(() => {
  if (props.format === 'bytes') return formatBytes(Number(props.value))
  if (props.format === 'string') return String(props.value)
  return Number(props.value).toLocaleString('fr-FR')
})
function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`
}
</script>
