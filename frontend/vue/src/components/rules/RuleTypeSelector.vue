<template>
  <div class="grid grid-cols-2 gap-4">
    <button
      v-for="rtype in types"
      :key="rtype.value"
      type="button"
      :class="[
        'flex flex-col gap-2 rounded-xl border p-5 text-left transition-all',
        modelValue === rtype.value
          ? 'border-[var(--accent)] bg-[var(--accent)]/10'
          : 'border-[var(--border)] bg-[var(--bg2)] hover:border-[var(--accent)]/50',
      ]"
      @click="$emit('update:modelValue', rtype.value)"
    >
      <i :class="['fas text-xl', rtype.icon, modelValue === rtype.value ? 'text-[var(--accent)]' : 'text-[var(--muted)]']" />
      <span class="font-semibold text-sm">{{ rtype.label }}</span>
      <span class="text-xs text-[var(--muted)]">{{ rtype.description }}</span>
    </button>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

defineProps({ modelValue: { type: String, default: '' } })
defineEmits(['update:modelValue'])

const { t } = useI18n()

const types = computed(() => [
  {
    value: 'simple',
    label: t('rules.simpleSection.title'),
    icon: 'fa-user-tag',
    description: t('rules.type.simpleDesc'),
  },
  {
    value: 'expert',
    label: t('rules.expertSection.title'),
    icon: 'fa-sliders',
    description: t('rules.type.expertDesc'),
  },
])
</script>
