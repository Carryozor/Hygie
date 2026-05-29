<template>
  <div class="flex items-start gap-2 bg-[var(--bg3)] border border-[var(--border)] rounded-lg p-3">
    <!-- Drag handle -->
    <button type="button" class="mt-1 cursor-grab text-[var(--muted)] hover:text-[var(--text)] touch-none" v-bind="dragHandle">
      <i class="fas fa-grip-vertical text-sm" />
    </button>

    <!-- Field -->
    <select
      :value="condition.field"
      class="flex-1 bg-[var(--bg2)] border border-[var(--border)] rounded-md px-2 py-1.5 text-sm focus:outline-none focus:border-[var(--accent)]"
      @change="update('field', $event.target.value)"
    >
      <option v-for="f in FIELDS" :key="f.value" :value="f.value">{{ f.label }}</option>
    </select>

    <!-- Operator -->
    <select
      :value="condition.op"
      class="w-28 bg-[var(--bg2)] border border-[var(--border)] rounded-md px-2 py-1.5 text-sm focus:outline-none focus:border-[var(--accent)]"
      @change="update('op', $event.target.value)"
    >
      <option v-for="o in availableOps" :key="o.value" :value="o.value">{{ o.label }}</option>
    </select>

    <!-- Value -->
    <input
      :value="valueDisplay"
      type="text"
      :placeholder="valuePlaceholder"
      class="w-32 bg-[var(--bg2)] border border-[var(--border)] rounded-md px-2 py-1.5 text-sm focus:outline-none focus:border-[var(--accent)]"
      @input="onValueInput($event.target.value)"
    />

    <!-- Remove -->
    <button
      type="button"
      class="mt-1 text-[var(--muted)] hover:text-red-400 transition-colors"
      @click="$emit('remove')"
    >
      <i class="fas fa-times text-sm" />
    </button>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  condition: { type: Object, required: true },
  dragHandle: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update', 'remove'])

const FIELDS = [
  { value: 'days_not_watched', label: 'Jours non-vu' },
  { value: 'play_count',       label: 'Nb lectures' },
  { value: 'rating',           label: 'Note' },
  { value: 'file_size_gb',     label: 'Taille (Go)' },
  { value: 'added_days_ago',   label: 'Ajouté il y a (jours)' },
  { value: 'media_type',       label: 'Type de média' },
  { value: 'seerr_user_id',    label: 'ID utilisateur Seerr' },
]

const LIST_OPS = ['in', 'not_in']

const ALL_OPS = [
  { value: 'gt',     label: '>' },
  { value: 'gte',    label: '>=' },
  { value: 'lt',     label: '<' },
  { value: 'lte',    label: '<=' },
  { value: 'eq',     label: '=' },
  { value: 'in',     label: 'dans' },
  { value: 'not_in', label: 'pas dans' },
]

const TEXT_FIELDS = ['media_type', 'seerr_user_id']

const availableOps = computed(() => {
  if (TEXT_FIELDS.includes(props.condition.field)) {
    return ALL_OPS.filter(o => ['eq', 'in', 'not_in'].includes(o.value))
  }
  return ALL_OPS
})

const isList = computed(() => LIST_OPS.includes(props.condition.op))

const valueDisplay = computed(() => {
  const v = props.condition.value
  if (Array.isArray(v)) return v.join(', ')
  return v ?? ''
})

const valuePlaceholder = computed(() =>
  isList.value ? '1, 2, 3' : 'valeur'
)

function update(key, val) {
  const next = { ...props.condition, [key]: val }
  if (key === 'op') {
    const nowList = LIST_OPS.includes(val)
    const wasArray = Array.isArray(next.value)
    if (nowList && !wasArray) next.value = []
    if (!nowList && wasArray) next.value = ''
  }
  emit('update', next)
}

function onValueInput(raw) {
  if (isList.value) {
    const parts = raw.split(',').map(s => s.trim()).filter(Boolean)
    const nums = parts.every(p => !isNaN(Number(p)))
    emit('update', { ...props.condition, value: nums ? parts.map(Number) : parts })
  } else {
    const n = Number(raw)
    emit('update', { ...props.condition, value: raw !== '' && !isNaN(n) ? n : raw })
  }
}
</script>
