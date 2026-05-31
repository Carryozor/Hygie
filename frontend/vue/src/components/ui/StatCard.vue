<template>
  <div class="bg-[var(--bg2)] border rounded-xl p-5 flex flex-col gap-2" :class="borderClass">
    <div class="flex items-center justify-between">
      <span class="text-xs uppercase tracking-wide" :class="labelClass">{{ label }}</span>
      <i :class="['fas', icon, 'opacity-70', iconClass]" />
    </div>
    <div class="text-3xl font-bold" :class="valueClass">{{ displayValue }}</div>
    <div v-if="sub" class="text-xs" :class="labelClass">{{ sub }}</div>
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
  color:  { type: String, default: 'accent' }, // accent | green | yellow | red | gray | blue
})

const COLOR_MAP = {
  accent: { border: 'border-[var(--border)]', label: 'text-[var(--muted)]', icon: 'text-[var(--accent)]', value: 'text-white' },
  green:  { border: 'border-green-500/30',    label: 'text-green-400/70',  icon: 'text-green-400',        value: 'text-green-300' },
  yellow: { border: 'border-yellow-500/30',   label: 'text-yellow-400/70', icon: 'text-yellow-400',       value: 'text-yellow-300' },
  red:    { border: 'border-red-500/30',      label: 'text-red-400/70',    icon: 'text-red-400',          value: 'text-red-300' },
  gray:   { border: 'border-[var(--border)]', label: 'text-[var(--muted)]',icon: 'text-[var(--muted)]',  value: 'text-[var(--muted)]' },
  blue:   { border: 'border-blue-500/30',     label: 'text-blue-400/70',   icon: 'text-blue-400',         value: 'text-blue-300' },
}

const theme = computed(() => COLOR_MAP[props.color] || COLOR_MAP.accent)
const borderClass = computed(() => theme.value.border)
const labelClass  = computed(() => theme.value.label)
const iconClass   = computed(() => theme.value.icon)
const valueClass  = computed(() => theme.value.value)

const displayValue = computed(() => {
  if (props.format === 'bytes')  return formatBytes(Number(props.value))
  if (props.format === 'string') return String(props.value)
  return Number(props.value).toLocaleString('fr-FR')
})

function formatBytes(b) {
  if (b < 1024)        return `${b} B`
  if (b < 1024 ** 2)   return `${(b / 1024).toFixed(1)} KB`
  if (b < 1024 ** 3)   return `${(b / 1024 ** 2).toFixed(1)} MB`
  return `${(b / 1024 ** 3).toFixed(1)} GB`
}
</script>
