<template>
  <div v-if="conditions.length" class="text-xs text-[var(--muted)] bg-[var(--bg3)] rounded-lg px-3 py-2 leading-relaxed">
    <span v-for="(c, i) in conditions" :key="i">
      <span v-if="i > 0" :class="operator === 'AND' ? 'text-blue-400' : 'text-orange-400'" class="font-semibold mx-1">
        {{ operator }}
      </span>
      <span class="text-[var(--text)]">{{ humanize(c) }}</span>
    </span>
  </div>
</template>

<script setup>
const props = defineProps({
  conditions: { type: Array, default: () => [] },
  operator:   { type: String, default: 'AND' },
})

const FIELD_LABELS = {
  days_not_watched: 'jours non-vu',
  play_count:       'nb lectures',
  rating:           'note',
  file_size_gb:     'taille (Go)',
  added_days_ago:   'ajouté il y a (j)',
  media_type:       'type',
  seerr_user_id:    'user Seerr',
}

const OP_LABELS = {
  gt: '>', gte: '≥', lt: '<', lte: '≤', eq: '=', in: '∈', not_in: '∉',
}

function humanize(c) {
  const field = FIELD_LABELS[c.field] ?? c.field
  const op = OP_LABELS[c.op] ?? c.op
  const val = Array.isArray(c.value) ? `{${c.value.join(', ')}}` : c.value
  return `${field} ${op} ${val}`
}
</script>
