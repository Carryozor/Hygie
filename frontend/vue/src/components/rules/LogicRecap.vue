<template>
  <div v-if="hasContent" class="text-xs text-[var(--muted)] bg-[var(--bg3)] rounded-lg px-3 py-2 leading-relaxed space-y-1">
    <!-- Bibliothèques ciblées -->
    <div v-if="libraryIds !== null" class="flex items-center gap-1.5 flex-wrap">
      <i class="fas fa-folder text-[9px] text-[var(--accent)]/70" />
      <span class="text-[var(--accent)]/80 font-medium">{{ t('rules.recap.libraries') }}</span>
      <template v-if="!libraryIds || libraryIds.length === 0">
        <span class="italic">{{ t('rules.recap.noneSelected') }}</span>
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
      <span class="italic">{{ t('rules.recap.allLibraries') }}</span>
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
import { useI18n } from 'vue-i18n'
import { useServersStore } from '@/stores/servers'

const { t } = useI18n()

const props = defineProps({
  conditionGroups: { type: Array,  default: () => [] },
  operator:        { type: String, default: 'AND' },
  libraryIds:      { type: Array,  default: null },
})

const serversStore = useServersStore()

const hasContent = computed(() =>
  props.conditionGroups.length > 0 || props.libraryIds !== undefined
)

const enabledServerIds = computed(() => {
  // Only show libraries from enabled servers — disabled servers are excluded
  // so rules don't display "Films" twice when one server is disabled.
  return new Set(
    (serversStore.servers || [])
      .filter(s => s.enabled !== false)
      .map(s => String(s.id))
  )
})

const libraryNames = computed(() => {
  if (!Array.isArray(props.libraryIds)) return []
  const allLibs = serversStore.libraries || []
  return props.libraryIds
    .map(id => allLibs.find(l => String(l.id) === String(id)))
    .filter(lib => lib && enabledServerIds.value.has(String(lib.server_id ?? '0')))
    .map(lib => lib.name)
})

const fieldLabels = computed(() => ({
  days_not_watched: t('conditions.daysNotWatchedShort'),
  play_count:       t('conditions.playCount'),
  rating:           t('conditions.rating'),
  file_size_gb:     t('conditions.fileSize'),
  added_days_ago:   t('conditions.addedDaysAgoShort'),
  media_type:       t('conditions.type'),
  seerr_user_id:    t('conditions.seerrUserShort'),
  never_watched:    t('conditions.neverWatched'),
}))

const OP_LABELS = {
  gt: '>', gte: '≥', lt: '<', lte: '≤', eq: '=', in: '∈', not_in: '∉',
}

function humanize(c) {
  const field = fieldLabels.value[c.field] ?? c.field
  const op    = OP_LABELS[c.op] ?? c.op
  const val   = Array.isArray(c.value) ? `{${c.value.join(', ')}}` : c.value
  return `${field} ${op} ${val}`
}
</script>
