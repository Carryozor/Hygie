<template>
  <div class="space-y-4">
    <!-- Rule meta -->
    <div class="grid grid-cols-2 gap-3">
      <div class="space-y-1">
        <label class="text-xs text-[var(--muted)] uppercase tracking-wide">Nom de la règle</label>
        <input
          v-model="form.name"
          type="text" placeholder="Ma règle…"
          class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]"
        />
      </div>
      <div class="space-y-1">
        <label class="text-xs text-[var(--muted)] uppercase tracking-wide">Action</label>
        <select
          v-model="form.action"
          class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]"
        >
          <option value="queue">Mettre en file de suppression</option>
          <option value="notify_only">Notifier seulement</option>
        </select>
      </div>
    </div>

    <!-- Library tree picker -->
    <div class="space-y-1">
      <label class="text-xs text-[var(--muted)] uppercase tracking-wide">
        Bibliothèques
        <span class="font-normal normal-case">
          ({{ form.library_ids === null ? 'toutes' : form.library_ids.length + ' sélectionnée(s)' }})
        </span>
      </label>
      <div class="bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-2 py-2 max-h-48 overflow-y-auto">
        <LibraryTreePicker v-model="form.library_ids" />
      </div>
    </div>

    <!-- Condition groups -->
    <div class="space-y-1">
      <label class="text-xs text-[var(--muted)] uppercase tracking-wide">Blocs de conditions</label>

      <div v-if="!form.condition_groups.length" class="text-xs text-[var(--muted)] text-center py-4">
        Aucun bloc — cliquez sur « Ajouter un bloc »
      </div>

      <template v-for="(grp, gi) in form.condition_groups" :key="gi">
        <!-- Inter-group connector (shown between groups) -->
        <div v-if="gi > 0" class="flex items-center gap-2 py-1 px-3">
          <div class="flex-1 h-px bg-[var(--border)]" />
          <button
            type="button"
            :class="[
              'px-3 py-0.5 rounded-full text-xs font-bold uppercase tracking-wider transition-colors',
              form.operator === 'AND'
                ? 'bg-blue-500/20 text-blue-400 border border-blue-500/40'
                : 'bg-orange-500/20 text-orange-400 border border-orange-500/40',
            ]"
            @click="form.operator = form.operator === 'AND' ? 'OR' : 'AND'"
          >{{ form.operator }}</button>
          <div class="flex-1 h-px bg-[var(--border)]" />
        </div>

        <!-- Group block -->
        <div class="border border-[var(--border)] rounded-xl bg-[var(--bg3)] overflow-hidden">
          <!-- Group header -->
          <div class="flex items-center justify-between px-3 py-2 bg-[var(--bg2)] border-b border-[var(--border)]">
            <span class="text-xs text-[var(--muted)] font-medium uppercase tracking-wide">Bloc {{ gi + 1 }}</span>
            <div class="flex items-center gap-2">
              <button
                type="button"
                class="text-xs text-[var(--accent)] hover:opacity-80 transition-opacity"
                @click="addCondition(gi)"
              >+ Condition</button>
              <button
                v-if="form.condition_groups.length > 1"
                type="button"
                class="text-[var(--muted)] hover:text-red-400 transition-colors"
                @click="removeGroup(gi)"
              >
                <i class="fas fa-times text-xs" />
              </button>
            </div>
          </div>

          <!-- Conditions inside group -->
          <div class="p-2 space-y-1">
            <div v-if="!grp.conditions.length" class="text-xs text-[var(--muted)] text-center py-2">
              Aucune condition
            </div>

            <template v-for="(cond, ci) in grp.conditions" :key="ci">
              <!-- Intra-group connector -->
              <div v-if="ci > 0" class="flex items-center gap-2 py-0.5 px-2">
                <div class="flex-1 h-px bg-[var(--border)]" />
                <button
                  type="button"
                  :class="[
                    'px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider transition-colors',
                    grp.operator === 'AND'
                      ? 'bg-blue-500/20 text-blue-400 border border-blue-500/40'
                      : 'bg-orange-500/20 text-orange-400 border border-orange-500/40',
                  ]"
                  @click="grp.operator = grp.operator === 'AND' ? 'OR' : 'AND'"
                >{{ grp.operator }}</button>
                <div class="flex-1 h-px bg-[var(--border)]" />
              </div>

              <ConditionCard
                :condition="cond"
                :seerr-users="seerrUsers"
                @update="updateCondition(gi, ci, $event)"
                @remove="removeCondition(gi, ci)"
              />
            </template>
          </div>
        </div>
      </template>

      <!-- Add group -->
      <button
        type="button"
        class="w-full mt-1 text-xs text-[var(--muted)] hover:text-[var(--accent)] border border-dashed border-[var(--border)] hover:border-[var(--accent)] rounded-lg py-2 transition-colors"
        @click="addGroup"
      >
        + Ajouter un bloc
      </button>
    </div>

    <!-- Logic recap -->
    <LogicRecap :condition-groups="form.condition_groups" :operator="form.operator" :library-ids="form.library_ids" />

    <!-- Priority + grace_days + enabled -->
    <div class="flex items-end gap-4">
      <div class="space-y-1 flex-1">
        <label class="text-xs text-[var(--muted)] uppercase tracking-wide">Priorité</label>
        <input
          v-model.number="form.priority"
          type="number" min="0" max="999"
          class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]"
        />
      </div>
      <div class="space-y-1 flex-1">
        <label class="text-xs text-[var(--muted)] uppercase tracking-wide">Délai de grâce (jours)</label>
        <input
          v-model.number="form.grace_days"
          type="number" min="0" max="365"
          class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]"
        />
      </div>
      <label class="flex items-center gap-3 cursor-pointer select-none pb-2">
        <input v-model="form.enabled" type="checkbox" class="hidden" />
        <span :class="['w-9 h-5 rounded-full flex items-center transition-colors duration-200', form.enabled ? 'bg-[var(--accent)]' : 'bg-[var(--border)]']">
          <span :class="['w-4 h-4 bg-white rounded-full shadow transition-transform duration-200 mx-0.5', form.enabled ? 'translate-x-4' : 'translate-x-0']" />
        </span>
        <span class="text-sm">Règle active</span>
      </label>
    </div>
  </div>
</template>

<script setup>
import { reactive, computed, watch, onMounted, ref } from 'vue'
import { useServersStore } from '@/stores/servers'
import ConditionCard from './ConditionCard.vue'
import LogicRecap from './LogicRecap.vue'
import LibraryTreePicker from './LibraryTreePicker.vue'
import api from '@/api/client'

const props = defineProps({
  initial: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:modelValue'])

const servers = useServersStore()
const seerrUsers = ref([])

function defaultGroup() {
  return { conditions: [{ field: 'days_not_watched', op: 'gt', value: 30 }], operator: 'AND' }
}

function initGroups(initial) {
  if (initial.condition_groups?.length) {
    return JSON.parse(JSON.stringify(initial.condition_groups))
  }
  // Backward compat: old format had flat conditions
  if (initial.conditions?.length) {
    return [{ conditions: JSON.parse(JSON.stringify(initial.conditions)), operator: initial.operator ?? 'AND' }]
  }
  return [defaultGroup()]
}

const form = reactive({
  name:             props.initial.name             ?? '',
  library_ids:      props.initial.library_ids      ?? null,
  condition_groups: initGroups(props.initial),
  operator:         props.initial.operator         ?? 'AND',
  action:           props.initial.action           ?? 'queue',
  grace_days:       props.initial.grace_days       ?? 7,
  enabled:          props.initial.enabled          !== false,
  priority:         props.initial.priority         ?? 0,
})

watch(form, () => emit('update:modelValue', {
  ...form,
  condition_groups: form.condition_groups.map(g => ({
    ...g,
    conditions: [...g.conditions],
  })),
}), { deep: true })

function addGroup() {
  form.condition_groups.push(defaultGroup())
}

function removeGroup(gi) {
  form.condition_groups.splice(gi, 1)
}

function addCondition(gi) {
  form.condition_groups[gi].conditions.push({ field: 'days_not_watched', op: 'gt', value: 30 })
}

function updateCondition(gi, ci, next) {
  form.condition_groups[gi].conditions[ci] = next
}

function removeCondition(gi, ci) {
  form.condition_groups[gi].conditions.splice(ci, 1)
  if (!form.condition_groups[gi].conditions.length) {
    // Keep at least one condition per group — add a default
    form.condition_groups[gi].conditions.push({ field: 'days_not_watched', op: 'gt', value: 30 })
  }
}

onMounted(async () => {
  if (!servers.libraries.length) await servers.fetch()
  try {
    const { data } = await api.get('/seerr-rules/users')
    seerrUsers.value = data || []
  } catch { /* silent — Seerr may not be configured */ }
})
</script>
