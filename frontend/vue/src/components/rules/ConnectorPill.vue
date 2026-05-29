<template>
  <div class="flex items-center gap-2 py-1 px-3">
    <div class="flex-1 h-px bg-[var(--border)]" />
    <button
      type="button"
      :class="[
        'px-3 py-0.5 rounded-full text-xs font-semibold uppercase tracking-wider transition-colors',
        modelValue === 'AND'
          ? 'bg-blue-500/20 text-blue-400 border border-blue-500/40'
          : 'bg-orange-500/20 text-orange-400 border border-orange-500/40',
      ]"
      @click="$emit('update:modelValue', modelValue === 'AND' ? 'OR' : 'AND')"
    >
      {{ modelValue }}
    </button>
    <div class="flex-1 h-px bg-[var(--border)]" />
  </div>
</template>

<script setup>
defineProps({ modelValue: { type: String, default: 'AND' } })
defineEmits(['update:modelValue'])
</script>
