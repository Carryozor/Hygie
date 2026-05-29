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

    <!-- Library -->
    <div class="space-y-1">
      <label class="text-xs text-[var(--muted)] uppercase tracking-wide">Bibliothèque <span class="font-normal normal-case">(optionnel)</span></label>
      <select
        v-model="form.library_id"
        class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]"
      >
        <option :value="null">— Toutes les bibliothèques —</option>
        <option v-for="lib in libraries" :key="lib.id" :value="lib.id">{{ lib.name }}</option>
      </select>
    </div>

    <!-- Conditions -->
    <div class="space-y-1">
      <div class="flex items-center justify-between">
        <label class="text-xs text-[var(--muted)] uppercase tracking-wide">Conditions</label>
        <button
          type="button"
          class="text-xs text-[var(--accent)] hover:opacity-80 transition-opacity"
          @click="addCondition"
        >
          + Ajouter
        </button>
      </div>

      <div v-if="!form.conditions.length" class="text-xs text-[var(--muted)] text-center py-4">
        Aucune condition — cliquez sur « Ajouter »
      </div>

      <template v-for="(cond, idx) in form.conditions" :key="idx">
        <ConnectorPill
          v-if="idx > 0"
          v-model="form.operator"
        />
        <ConditionCard
          :condition="cond"
          @update="updateCondition(idx, $event)"
          @remove="removeCondition(idx)"
        />
      </template>
    </div>

    <!-- Logic recap -->
    <LogicRecap :conditions="form.conditions" :operator="form.operator" />

    <!-- Priority + enabled -->
    <div class="flex items-center gap-4">
      <div class="space-y-1 flex-1">
        <label class="text-xs text-[var(--muted)] uppercase tracking-wide">Priorité</label>
        <input
          v-model.number="form.priority"
          type="number" min="0" max="999"
          class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]"
        />
      </div>
      <label class="flex items-center gap-3 cursor-pointer select-none mt-5">
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
import { reactive, computed, watch, onMounted } from 'vue'
import { useServersStore } from '@/stores/servers'
import ConditionCard from './ConditionCard.vue'
import ConnectorPill from './ConnectorPill.vue'
import LogicRecap from './LogicRecap.vue'

const props = defineProps({
  initial: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:modelValue'])

const servers = useServersStore()
const libraries = computed(() => servers.libraries)

const form = reactive({
  name:       props.initial.name       ?? '',
  library_id: props.initial.library_id ?? null,
  conditions: props.initial.conditions ? JSON.parse(JSON.stringify(props.initial.conditions)) : [],
  operator:   props.initial.operator   ?? 'AND',
  action:     props.initial.action     ?? 'queue',
  enabled:    props.initial.enabled    !== false,
  priority:   props.initial.priority   ?? 0,
})

watch(form, () => emit('update:modelValue', { ...form, conditions: [...form.conditions] }), { deep: true })

function addCondition() {
  form.conditions.push({ field: 'days_not_watched', op: 'gt', value: 30 })
}

function updateCondition(idx, next) {
  form.conditions[idx] = next
}

function removeCondition(idx) {
  form.conditions.splice(idx, 1)
}

onMounted(async () => {
  if (!servers.libraries.length) await servers.fetch()
})
</script>
