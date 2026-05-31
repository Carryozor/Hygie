<template>
  <div v-if="hasContent" class="text-xs text-[var(--muted)] bg-[var(--bg3)] rounded-lg px-3 py-2 leading-relaxed space-y-1">
    <!-- Bibliothèques ciblées -->
    <div v-if="libraryIds !== null" class="flex items-center gap-1.5 flex-wrap">
      <i class="fas fa-folder text-[9px] text-[var(--accent)]/70" />
      <span class="text-[var(--accent)]/80 font-medium">Bib. :</span>
      <template v-if="!libraryIds || libraryIds.length === 0">
        <span class="italic">aucune sélectionnée</span>
      </template>
      <template v-else>
        <span
          v-for="(name, i) in libraryNames"
          :key="i"
          class="inline-flex items-center gap-1 bg-[var(--accent)]/10 text-[var(--accent)] rounded px-1.5 py-0.5"
        >{{ name }}</span>
      </template>
    </div>
    <div v-else class="flex items-center gap-1.5">
      <i class="fas fa-folder-open text-[9px] text-[var(--muted)]" />
      <span class="italic">Toutes les bibliothèques</span>
    </div>

    <!-- Résumé des conditions -->
    <div v-if="conditionGroups.length">
      <template v-for="(grp, gi) in conditionGroups" :key="gi">
        <span v-if="gi > 0" :class="operator === 'AND' ? 'text-blue-400' : 'text-orange-400'" class="font-bold mx-1">
          {{ operator }}
        </span>
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
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useServersStore } from '@/stores/servers'

const props = defineProps({
  conditionGroups: { type: Array,  default: () => [] },
  operator:        { type: String, default: 'AND' },
  libraryIds:      { type: Array,  default: null },  // null = all; [] or string[] = specific
})

const serversStore = useServersStore()

const hasContent = computed(() =>
  props.conditionGroups.length > 0 || props.libraryIds !== undefined
)

// Resolve library IDs to names using the servers store
const libraryNames = computed(() => {
  if (!Array.isArray(props.libraryIds)) return []
  const allLibs = serversStore.libraries || []
  return props.libraryIds.map(id => {
    const lib = allLibs.find(l => String(l.id) === String(id))
    return lib ? lib.name : id
  })
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
  const op    = OP_LABELS[c.op] ?? c.op
  const val   = Array.isArray(c.value) ? `{${c.value.join(', ')}}` : c.value
  return `${field} ${op} ${val}`
}
</script>
