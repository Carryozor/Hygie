<template>
  <div class="bg-[var(--bg3)] border border-[var(--border)] rounded-lg p-3 space-y-2">
    <!-- Main row: drag handle + field + op + [plain value] + remove -->
    <div class="flex items-start gap-2">
      <!-- Drag handle -->
      <button type="button" class="mt-1 cursor-grab text-[var(--muted)] hover:text-[var(--text)] touch-none flex-shrink-0" v-bind="dragHandle">
        <i class="fas fa-grip-vertical text-sm" />
      </button>

      <!-- Field -->
      <select
        :value="condition.field"
        class="flex-1 bg-[var(--bg2)] border border-[var(--border)] rounded-md px-2 py-1.5 text-sm focus:outline-none focus:border-[var(--accent)]"
        @change="update('field', $event.target.value)"
      >
        <option v-for="f in fields" :key="f.value" :value="f.value">{{ f.label }}</option>
      </select>

      <!-- Operator -->
      <select
        :value="condition.op"
        class="w-28 bg-[var(--bg2)] border border-[var(--border)] rounded-md px-2 py-1.5 text-sm focus:outline-none focus:border-[var(--accent)]"
        @change="update('op', $event.target.value)"
      >
        <option v-for="o in availableOps" :key="o.value" :value="o.value">{{ o.label }}</option>
      </select>

      <!-- Value: plain input (only when not showing picker) -->
      <input
        v-if="!isSeerrUserPicker"
        :value="valueDisplay"
        type="text"
        :placeholder="valuePlaceholder"
        class="w-32 bg-[var(--bg2)] border border-[var(--border)] rounded-md px-2 py-1.5 text-sm focus:outline-none focus:border-[var(--accent)]"
        @input="onValueInput($event.target.value)"
      />

      <!-- Remove -->
      <button
        type="button"
        class="mt-1 flex-shrink-0 text-[var(--muted)] hover:text-red-400 transition-colors"
        @click="$emit('remove')"
      >
        <i class="fas fa-xmark text-sm" />
      </button>
    </div>

    <!-- Seerr user picker: full-width second row -->
    <div v-if="isSeerrUserPicker" class="space-y-1.5">
      <div class="flex items-center gap-2">
        <button
          type="button"
          class="text-[10px] px-2 py-0.5 rounded border border-[var(--border)] text-[var(--muted)] hover:text-white transition-colors"
          @click="displayMode = displayMode === 'id' ? 'name' : 'id'"
        >
          {{ displayMode === 'id' ? t('conditions.showNames') : t('conditions.showIds') }}
        </button>
        <span v-if="selectedIds.length" class="text-[10px] text-[var(--accent)]">{{ t('conditions.selectedCount', { n: selectedIds.length }) }}</span>
      </div>
      <div class="max-h-36 overflow-y-auto rounded-md border border-[var(--border)] bg-[var(--bg2)] divide-y divide-[var(--border)]">
        <label
          v-for="u in seerrUsers"
          :key="u.id"
          class="flex items-center gap-2.5 px-2.5 py-1.5 cursor-pointer hover:bg-[var(--bg3)] transition-colors"
          :class="selectedIds.includes(u.id) ? 'bg-[var(--accent)]/5' : ''"
        >
          <input
            type="checkbox"
            :checked="selectedIds.includes(u.id)"
            class="accent-[var(--accent)]"
            @change="toggleUser(u.id)"
          />
          <span class="text-xs truncate">{{ displayMode === 'id' ? u.id : u.username }}</span>
          <span v-if="displayMode === 'name'" class="text-[10px] text-[var(--muted)] ml-auto flex-shrink-0">#{{ u.id }}</span>
        </label>
        <div v-if="!seerrUsers.length" class="text-xs text-[var(--muted)] text-center py-2">{{ t('conditions.noUsers') }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const props = defineProps({
  condition:   { type: Object, required: true },
  dragHandle:  { type: Object, default: () => ({}) },
  seerrUsers:  { type: Array, default: () => [] },
})
const emit = defineEmits(['update', 'remove'])

const displayMode = ref('name') // 'id' | 'name'

const fields = computed(() => [
  { value: 'days_not_watched', label: t('conditions.daysNotWatched') },
  { value: 'play_count',       label: t('conditions.playCount') },
  { value: 'rating',           label: t('conditions.rating') },
  { value: 'file_size_gb',     label: t('conditions.fileSize') },
  { value: 'added_days_ago',   label: t('conditions.addedDaysAgo') },
  { value: 'media_type',       label: t('conditions.mediaType') },
  { value: 'seerr_user_id',    label: t('conditions.seerrUser') },
])

const LIST_OPS = ['in', 'not_in']

const ALL_OPS = computed(() => [
  { value: 'gt',     label: '>' },
  { value: 'gte',    label: '>=' },
  { value: 'lt',     label: '<' },
  { value: 'lte',    label: '<=' },
  { value: 'eq',     label: '=' },
  { value: 'in',     label: t('operators.in') },
  { value: 'not_in', label: t('operators.notIn') },
])

const TEXT_FIELDS = ['media_type', 'seerr_user_id']

const availableOps = computed(() => {
  if (TEXT_FIELDS.includes(props.condition.field)) {
    return ALL_OPS.value.filter(o => ['eq', 'in', 'not_in'].includes(o.value))
  }
  return ALL_OPS.value
})

const isList = computed(() => LIST_OPS.includes(props.condition.op))

const isSeerrUserPicker = computed(() =>
  props.condition.field === 'seerr_user_id' &&
  isList.value &&
  props.seerrUsers.length > 0
)

const selectedIds = computed(() => {
  const v = props.condition.value
  if (!Array.isArray(v)) return []
  return v.map(Number).filter(n => !isNaN(n))
})

function toggleUser(id) {
  const current = [...selectedIds.value]
  const idx = current.indexOf(id)
  if (idx === -1) current.push(id)
  else current.splice(idx, 1)
  emit('update', { ...props.condition, value: current })
}

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
