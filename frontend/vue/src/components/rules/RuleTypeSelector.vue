<template>
  <div class="grid grid-cols-2 gap-4">
    <button
      v-for="t in types"
      :key="t.value"
      type="button"
      :class="[
        'flex flex-col gap-2 rounded-xl border p-5 text-left transition-all',
        modelValue === t.value
          ? 'border-[var(--accent)] bg-[var(--accent)]/10'
          : 'border-[var(--border)] bg-[var(--bg2)] hover:border-[var(--accent)]/50',
      ]"
      @click="$emit('update:modelValue', t.value)"
    >
      <i :class="['fas text-xl', t.icon, modelValue === t.value ? 'text-[var(--accent)]' : 'text-[var(--muted)]']" />
      <span class="font-semibold text-sm">{{ t.label }}</span>
      <span class="text-xs text-[var(--muted)]">{{ t.description }}</span>
    </button>
  </div>
</template>

<script setup>
defineProps({ modelValue: { type: String, default: '' } })
defineEmits(['update:modelValue'])

const types = [
  {
    value: 'simple',
    label: 'Règle Simple',
    icon: 'fa-user-tag',
    description: 'Délai de grâce personnalisé par utilisateur Seerr et bibliothèque.',
  },
  {
    value: 'expert',
    label: 'Règle Experte',
    icon: 'fa-sliders',
    description: 'Conditions avancées sur jours non-vu, note, taille, type de média…',
  },
]
</script>
