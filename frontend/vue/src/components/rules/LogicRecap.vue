<template>
  <div v-if="conditionGroups.length" class="text-xs text-[var(--muted)] bg-[var(--bg3)] rounded-lg px-3 py-2 leading-relaxed">
    <template v-for="(grp, gi) in conditionGroups" :key="gi">
      <span v-if="gi > 0" :class="operator === 'AND' ? 'text-blue-400' : 'text-orange-400'" class="font-bold mx-1">
        {{ operator }}
      </span>
      <!-- Wrap in parens when there are multiple conditions in a group or multiple groups -->
      <span v-if="grp.conditions.length > 1 || conditionGroups.length > 1">(</span>
      <span v-for="(c, ci) in grp.conditions" :key="ci">
        <span v-if="ci > 0" :class="grp.operator === 'AND' ? 'text-blue-400' : 'text-orange-400'" class="font-semibold mx-1">
          {{ grp.operator }}
        </span>
        <span class="text-[var(--text)]">{{ humanize(c) }}</span>
      </span>
      <span v-if="grp.conditions.length > 1 || conditionGroups.length > 1">)</span>
    </template>
  </div>
</template>

<script setup>
const props = defineProps({
  conditionGroups: { type: Array, default: () => [] },
  operator:        { type: String, default: 'AND' },
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
