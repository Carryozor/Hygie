# Hygie v3.0 — Phase 4: Unified Rule Builder

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the unified rule creation UI in Vue 3 where the user chooses between a "Simple rule" (existing Seerr/calendar-based) and an "Expert rule" (visual card builder from v2.8), with a logical recap shown at the bottom.

**Architecture:** A single "Create Rule" modal presents a type selector. Simple rules use the existing seerr_rules API (`/api/seerr-rules`). Expert rules use the expert_rules API (`/api/expert-rules`). The `RulesView.vue` (Phase 3 stub) is replaced with a full implementation showing both rule types in a unified list, differentiated by a badge. The visual expert rule builder uses Vue 3 drag-and-drop with native HTML5 DnD API (no extra library). The logical recap displays a human-readable summary of the built conditions.

**Tech Stack:** Vue 3, Pinia, HTML5 Drag and Drop API, existing FastAPI endpoints (no backend changes needed — all APIs were built in v2.8)

**Prerequisite:** Phase 3 (Vue 3 frontend) must be complete.

---

## File Structure

```
frontend/vue/src/
├── stores/
│   ├── rules.js                    ← Pinia: simple + expert rules
├── components/
│   └── rules/
│       ├── RuleTypeSelector.vue    ← Step 1 of create modal: choose simple/expert
│       ├── SimpleRuleForm.vue      ← Form for seerr_rules (watched %, grace days)
│       ├── ExpertRuleBuilder.vue   ← Visual card builder (Rule Builder B)
│       ├── ConditionCard.vue       ← Single draggable condition card
│       ├── ConnectorPill.vue       ← AND/OR pill between cards
│       ├── LogicRecap.vue          ← Human-readable rule summary
│       └── CreateRuleModal.vue     ← Full modal wrapping all the above
└── views/
    └── RulesView.vue               ← Replace stub with full implementation
```

---

### Task 1: Pinia rules store

**Files:**
- Create: `frontend/vue/src/stores/rules.js`

- [ ] **Step 1: Write the store**

```javascript
// frontend/vue/src/stores/rules.js
import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api/client'

export const useRulesStore = defineStore('rules', () => {
  const simpleRules  = ref([])
  const expertRules  = ref([])
  const loading      = ref(false)

  async function fetchAll() {
    loading.value = true
    try {
      const [sRes, eRes] = await Promise.all([
        api.get('/seerr-rules').catch(() => ({ data: [] })),
        api.get('/expert-rules'),
      ])
      simpleRules.value = sRes.data || []
      expertRules.value = eRes.data || []
    } finally {
      loading.value = false
    }
  }

  // Simple rules (seerr_rules)
  async function createSimpleRule(payload) {
    const { data } = await api.post('/seerr-rules', payload)
    simpleRules.value.push(data)
    return data
  }

  async function updateSimpleRule(id, payload) {
    const { data } = await api.put(`/seerr-rules/${id}`, payload)
    const idx = simpleRules.value.findIndex(r => r.id === id)
    if (idx >= 0) simpleRules.value[idx] = data
    return data
  }

  async function deleteSimpleRule(id) {
    await api.delete(`/seerr-rules/${id}`)
    simpleRules.value = simpleRules.value.filter(r => r.id !== id)
  }

  // Expert rules
  async function createExpertRule(payload) {
    const { data } = await api.post('/expert-rules', payload)
    expertRules.value.push(data)
    return data
  }

  async function updateExpertRule(id, payload) {
    const { data } = await api.put(`/expert-rules/${id}`, payload)
    const idx = expertRules.value.findIndex(r => r.id === id)
    if (idx >= 0) expertRules.value[idx] = data
    return data
  }

  async function deleteExpertRule(id) {
    await api.delete(`/expert-rules/${id}`)
    expertRules.value = expertRules.value.filter(r => r.id !== id)
  }

  async function toggleExpertRule(id) {
    const rule = expertRules.value.find(r => r.id === id)
    if (!rule) return
    await updateExpertRule(id, { ...rule, enabled: !rule.enabled })
  }

  return {
    simpleRules, expertRules, loading,
    fetchAll,
    createSimpleRule, updateSimpleRule, deleteSimpleRule,
    createExpertRule, updateExpertRule, deleteExpertRule, toggleExpertRule,
  }
})
```

- [ ] **Step 2: Build to verify**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | grep -c "✓"
```
Expected: `1`

- [ ] **Step 3: Commit**

```bash
cd /opt/claude/hygie && git add frontend/vue/src/stores/rules.js
git commit -m "feat(rules): Pinia rules store (simple + expert CRUD)"
```

---

### Task 2: Rule type selector + Simple rule form

**Files:**
- Create: `frontend/vue/src/components/rules/RuleTypeSelector.vue`
- Create: `frontend/vue/src/components/rules/SimpleRuleForm.vue`

- [ ] **Step 1: Create `RuleTypeSelector.vue`**

```vue
<!-- frontend/vue/src/components/rules/RuleTypeSelector.vue -->
<template>
  <div class="grid grid-cols-2 gap-4">
    <button
      @click="$emit('select', 'simple')"
      class="flex flex-col items-start gap-3 p-5 rounded-xl border-2 transition-all hover:border-[var(--accent)] hover:bg-[var(--accent)]/5"
      :class="selected === 'simple' ? 'border-[var(--accent)] bg-[var(--accent)]/5' : 'border-[var(--border)] bg-[var(--bg3)]'"
    >
      <div class="flex items-center justify-between w-full">
        <i class="fas fa-sliders-h text-2xl text-[var(--accent)]" />
        <span v-if="selected === 'simple'" class="text-xs bg-[var(--accent)] px-2 py-0.5 rounded text-white">Sélectionné</span>
      </div>
      <div>
        <div class="font-semibold text-sm">Règle Simple</div>
        <div class="text-xs text-[var(--muted)] mt-1">
          Basée sur le taux de visionnage, les seuils Seerr et les jours de grâce. Idéale pour une gestion automatique standard.
        </div>
      </div>
    </button>

    <button
      @click="$emit('select', 'expert')"
      class="flex flex-col items-start gap-3 p-5 rounded-xl border-2 transition-all hover:border-purple-500 hover:bg-purple-500/5"
      :class="selected === 'expert' ? 'border-purple-500 bg-purple-500/5' : 'border-[var(--border)] bg-[var(--bg3)]'"
    >
      <div class="flex items-center justify-between w-full">
        <i class="fas fa-code-branch text-2xl text-purple-400" />
        <span v-if="selected === 'expert'" class="text-xs bg-purple-500 px-2 py-0.5 rounded text-white">Sélectionné</span>
      </div>
      <div>
        <div class="font-semibold text-sm">Règle Experte</div>
        <div class="text-xs text-[var(--muted)] mt-1">
          Constructeur visuel avec conditions personnalisées, opérateurs ET/OU, glisser-déposer pour réordonner.
        </div>
      </div>
    </button>
  </div>
</template>

<script setup>
defineProps({ selected: { type: String, default: '' } })
defineEmits(['select'])
</script>
```

- [ ] **Step 2: Create `SimpleRuleForm.vue`**

```vue
<!-- frontend/vue/src/components/rules/SimpleRuleForm.vue -->
<template>
  <div class="space-y-5">
    <div>
      <label class="block text-xs text-[var(--muted)] mb-1">Nom de la règle</label>
      <input v-model="form.name" type="text" placeholder="Ma règle simple"
        class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-[var(--accent)]" />
    </div>

    <div>
      <label class="block text-xs text-[var(--muted)] mb-1">Bibliothèque</label>
      <select v-model="form.library_id"
        class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm">
        <option value="">Toutes les bibliothèques</option>
        <option v-for="lib in servers.libraries" :key="lib.id" :value="lib.id">{{ lib.name }}</option>
      </select>
    </div>

    <div class="grid grid-cols-2 gap-4">
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Jours de grâce</label>
        <input v-model.number="form.grace_days" type="number" min="1" max="3650"
          class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm" />
      </div>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">% visionné minimum</label>
        <input v-model.number="form.watched_percent" type="number" min="0" max="100"
          class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm" />
      </div>
    </div>

    <div class="flex items-center justify-between">
      <div>
        <div class="text-sm font-medium">Activer la règle</div>
        <div class="text-xs text-[var(--muted)]">La règle sera appliquée lors du prochain scan</div>
      </div>
      <ToggleSlider v-model="form.enabled" />
    </div>
  </div>
</template>

<script setup>
import { reactive, watch } from 'vue'
import { useServersStore } from '@/stores/servers'
import ToggleSlider from '@/components/ui/ToggleSlider.vue'

const props = defineProps({ modelValue: { type: Object, default: () => ({}) } })
const emit  = defineEmits(['update:modelValue'])

const servers = useServersStore()

const form = reactive({
  name:            props.modelValue.name || '',
  library_id:      props.modelValue.library_id || '',
  grace_days:      props.modelValue.grace_days ?? 30,
  watched_percent: props.modelValue.watched_percent ?? 80,
  enabled:         props.modelValue.enabled ?? true,
})

watch(form, val => emit('update:modelValue', { ...val }), { deep: true })
</script>
```

- [ ] **Step 3: Build to verify**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | grep -c "✓"
```

- [ ] **Step 4: Commit**

```bash
cd /opt/claude/hygie && git add frontend/vue/src/components/rules/
git commit -m "feat(rules): RuleTypeSelector + SimpleRuleForm components"
```

---

### Task 3: Expert rule builder — condition cards + drag-and-drop + connectors

**Files:**
- Create: `frontend/vue/src/components/rules/ConditionCard.vue`
- Create: `frontend/vue/src/components/rules/ConnectorPill.vue`
- Create: `frontend/vue/src/components/rules/ExpertRuleBuilder.vue`

Expert rule builder fields for each condition:
- `field`: dropdown — `jours_non_vu` (days since last play), `date_ajout_jours` (days since added), `note` (rating 0-10), `type` (movie/series/episode), `duree_minutes` (duration in minutes), `view_count` (number of plays), `titre` (title contains)
- `operator`: `>`, `<`, `>=`, `<=`, `=`, `!=`, `CONTAINS`, `NOT_CONTAINS`, `IN`, `NOT_IN`
- `value`: string or number input (or comma-separated list for IN/NOT_IN)

- [ ] **Step 1: Create `ConditionCard.vue`**

```vue
<!-- frontend/vue/src/components/rules/ConditionCard.vue -->
<template>
  <div
    class="flex items-center gap-3 p-3 rounded-xl border border-[var(--border)] bg-[var(--bg3)] group cursor-grab active:cursor-grabbing select-none"
    :class="{ 'opacity-50': dragging }"
    draggable="true"
    @dragstart="$emit('dragstart', $event)"
    @dragend="$emit('dragend', $event)"
    @dragover.prevent
    @drop="$emit('drop', $event)"
  >
    <!-- Drag handle -->
    <div class="text-[var(--muted)] opacity-0 group-hover:opacity-100 transition-opacity cursor-grab">
      <i class="fas fa-grip-vertical text-xs" />
    </div>

    <!-- Field selector -->
    <select v-model="cond.field" @change="onFieldChange"
      class="bg-[var(--bg2)] border border-[var(--border)] rounded-lg px-2 py-1.5 text-xs flex-shrink-0 min-w-[140px]">
      <option v-for="f in FIELDS" :key="f.value" :value="f.value">{{ f.label }}</option>
    </select>

    <!-- Operator selector -->
    <select v-model="cond.operator"
      class="bg-[var(--bg2)] border border-[var(--border)] rounded-lg px-2 py-1.5 text-xs flex-shrink-0 w-20">
      <option v-for="op in availableOperators" :key="op" :value="op">{{ op }}</option>
    </select>

    <!-- Value input -->
    <input
      v-model="cond.value"
      :type="valueInputType"
      :placeholder="valuePlaceholder"
      class="bg-[var(--bg2)] border border-[var(--border)] rounded-lg px-3 py-1.5 text-xs flex-1 min-w-0 focus:outline-none focus:border-[var(--accent)]"
    />

    <!-- Remove button -->
    <button @click="$emit('remove')"
      class="text-[var(--muted)] hover:text-red-400 transition-colors flex-shrink-0 opacity-0 group-hover:opacity-100">
      <i class="fas fa-times text-xs" />
    </button>
  </div>
</template>

<script setup>
import { reactive, computed, watch } from 'vue'

const FIELDS = [
  { value: 'jours_non_vu',     label: 'Jours sans visionnage',   type: 'number' },
  { value: 'date_ajout_jours', label: 'Jours depuis l\'ajout',   type: 'number' },
  { value: 'note',             label: 'Note (0-10)',              type: 'number' },
  { value: 'view_count',       label: 'Nombre de lectures',      type: 'number' },
  { value: 'duree_minutes',    label: 'Durée (minutes)',          type: 'number' },
  { value: 'type',             label: 'Type de média',            type: 'enum'   },
  { value: 'titre',            label: 'Titre',                    type: 'string' },
]

const NUMERIC_OPS = ['>', '<', '>=', '<=', '=', '!=']
const STRING_OPS  = ['=', '!=', 'CONTAINS', 'NOT_CONTAINS']
const ENUM_OPS    = ['=', '!=', 'IN', 'NOT_IN']

const props = defineProps({
  modelValue: { type: Object, required: true },
  dragging:   { type: Boolean, default: false },
})
const emit  = defineEmits(['update:modelValue', 'remove', 'dragstart', 'dragend', 'drop'])

const cond = reactive({ ...props.modelValue })
watch(cond, v => emit('update:modelValue', { ...v }), { deep: true })

const fieldMeta = computed(() => FIELDS.find(f => f.value === cond.field) || FIELDS[0])

const availableOperators = computed(() => {
  if (fieldMeta.value.type === 'string') return STRING_OPS
  if (fieldMeta.value.type === 'enum')   return ENUM_OPS
  return NUMERIC_OPS
})

const valueInputType   = computed(() => fieldMeta.value.type === 'number' ? 'number' : 'text')
const valuePlaceholder = computed(() => {
  if (cond.operator === 'IN' || cond.operator === 'NOT_IN') return 'val1, val2, val3'
  if (cond.field === 'type') return 'movie / series / episode'
  return ''
})

function onFieldChange() {
  cond.operator = availableOperators.value[0]
  cond.value    = ''
}
</script>
```

- [ ] **Step 2: Create `ConnectorPill.vue`**

```vue
<!-- frontend/vue/src/components/rules/ConnectorPill.vue -->
<template>
  <div class="flex items-center justify-center py-1">
    <button
      @click="toggle"
      class="px-3 py-1 rounded-full text-xs font-bold tracking-wider border transition-all"
      :class="modelValue === 'AND'
        ? 'bg-blue-500/20 border-blue-500/40 text-blue-400 hover:bg-blue-500/30'
        : 'bg-orange-500/20 border-orange-500/40 text-orange-400 hover:bg-orange-500/30'"
    >
      {{ modelValue }}
    </button>
  </div>
</template>

<script setup>
const props = defineProps({ modelValue: { type: String, default: 'AND' } })
const emit  = defineEmits(['update:modelValue'])
function toggle() { emit('update:modelValue', props.modelValue === 'AND' ? 'OR' : 'AND') }
</script>
```

- [ ] **Step 3: Create `LogicRecap.vue`**

```vue
<!-- frontend/vue/src/components/rules/LogicRecap.vue -->
<template>
  <div v-if="conditions.length" class="rounded-xl border border-[var(--border)] bg-[var(--bg3)]/50 p-4 space-y-2">
    <div class="text-xs font-semibold text-[var(--muted)] uppercase tracking-widest">Récapitulatif logique</div>
    <div class="text-sm font-mono text-slate-300">
      <span v-for="(cond, i) in conditions" :key="i">
        <span class="text-[var(--accent)]">{{ fieldLabel(cond.field) }}</span>
        <span class="text-yellow-400 mx-1">{{ cond.operator }}</span>
        <span class="text-green-400">{{ cond.value }}</span>
        <span v-if="i < conditions.length - 1" class="mx-2 font-bold"
          :class="operator === 'AND' ? 'text-blue-400' : 'text-orange-400'">
          {{ operator }}
        </span>
      </span>
    </div>
    <div class="text-xs text-[var(--muted)] border-t border-[var(--border)] pt-2">
      → Action: <span class="text-[var(--accent)]">{{ actionLabel }}</span>
      · <span>{{ conditions.length }} condition{{ conditions.length > 1 ? 's' : '' }}</span>
      · <span>Priorité {{ priority }}</span>
    </div>
  </div>
</template>

<script setup>
const FIELD_LABELS = {
  jours_non_vu:     'jours_non_vu',
  date_ajout_jours: 'date_ajout_jours',
  note:             'note',
  view_count:       'view_count',
  duree_minutes:    'duree_minutes',
  type:             'type',
  titre:            'titre',
}
const ACTION_LABELS = {
  queue:       'File de suppression',
  notify_only: 'Notification seule',
  ignore:      'Ignorer',
}

const props = defineProps({
  conditions: { type: Array, default: () => [] },
  operator:   { type: String, default: 'AND' },
  action:     { type: String, default: 'queue' },
  priority:   { type: Number, default: 0 },
})

const fieldLabel  = f => FIELD_LABELS[f] || f
const actionLabel = props.action ? (ACTION_LABELS[props.action] || props.action) : 'File de suppression'
</script>
```

- [ ] **Step 4: Create `ExpertRuleBuilder.vue`**

```vue
<!-- frontend/vue/src/components/rules/ExpertRuleBuilder.vue -->
<template>
  <div class="space-y-4">
    <!-- Rule name + meta -->
    <div class="grid grid-cols-2 gap-4">
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Nom de la règle</label>
        <input v-model="form.name" type="text" placeholder="Règle experte"
          class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-purple-500" />
      </div>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Action</label>
        <select v-model="form.action"
          class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm">
          <option value="queue">File de suppression</option>
          <option value="notify_only">Notification seule</option>
          <option value="ignore">Ignorer</option>
        </select>
      </div>
    </div>

    <div class="grid grid-cols-2 gap-4">
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Priorité (0 = la plus haute)</label>
        <input v-model.number="form.priority" type="number" min="0"
          class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm" />
      </div>
      <div class="flex items-end pb-1">
        <div class="flex items-center gap-3">
          <ToggleSlider v-model="form.enabled" />
          <span class="text-sm">{{ form.enabled ? 'Activée' : 'Désactivée' }}</span>
        </div>
      </div>
    </div>

    <!-- Conditions builder -->
    <div>
      <div class="flex items-center justify-between mb-3">
        <label class="text-xs text-[var(--muted)] uppercase tracking-widest">Conditions</label>
        <button @click="addCondition"
          class="text-xs bg-purple-500/20 border border-purple-500/30 text-purple-400 hover:bg-purple-500/30 px-3 py-1.5 rounded-lg transition-colors flex items-center gap-1.5">
          <i class="fas fa-plus" />
          Ajouter une condition
        </button>
      </div>

      <!-- Empty state -->
      <div v-if="!conditions.length"
        class="border-2 border-dashed border-[var(--border)] rounded-xl p-8 text-center text-[var(--muted)] text-sm">
        Aucune condition. Cliquez sur « Ajouter une condition » pour commencer.
      </div>

      <!-- Condition cards with connectors -->
      <div v-else class="space-y-0">
        <template v-for="(cond, i) in conditions" :key="cond._key">
          <ConditionCard
            v-model="conditions[i]"
            :dragging="draggingIdx === i"
            @remove="removeCondition(i)"
            @dragstart="onDragStart($event, i)"
            @dragend="onDragEnd"
            @drop="onDrop($event, i)"
          />
          <ConnectorPill
            v-if="i < conditions.length - 1"
            v-model="form.operator"
          />
        </template>
      </div>
    </div>

    <!-- Logical recap -->
    <LogicRecap
      :conditions="conditions"
      :operator="form.operator"
      :action="form.action"
      :priority="form.priority"
    />
  </div>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'
import ConditionCard from './ConditionCard.vue'
import ConnectorPill from './ConnectorPill.vue'
import LogicRecap    from './LogicRecap.vue'
import ToggleSlider  from '@/components/ui/ToggleSlider.vue'

const props = defineProps({ modelValue: { type: Object, default: () => ({}) } })
const emit  = defineEmits(['update:modelValue'])

const form = reactive({
  name:      props.modelValue.name || '',
  operator:  props.modelValue.operator || 'AND',
  action:    props.modelValue.action || 'queue',
  priority:  props.modelValue.priority ?? 0,
  enabled:   props.modelValue.enabled ?? true,
})

let _keyCounter = 0
const conditions = ref(
  (props.modelValue.conditions || []).map(c => ({ ...c, _key: ++_keyCounter }))
)

watch([form, conditions], () => {
  emit('update:modelValue', {
    ...form,
    conditions: conditions.value.map(({ _key, ...rest }) => rest),
  })
}, { deep: true })

function addCondition() {
  conditions.value.push({
    _key:     ++_keyCounter,
    field:    'jours_non_vu',
    operator: '>',
    value:    '365',
  })
}

function removeCondition(i) {
  conditions.value.splice(i, 1)
}

// Drag-and-drop reorder
const draggingIdx = ref(-1)

function onDragStart(evt, i) {
  draggingIdx.value = i
  evt.dataTransfer.effectAllowed = 'move'
  evt.dataTransfer.setData('text/plain', String(i))
}

function onDragEnd() {
  draggingIdx.value = -1
}

function onDrop(evt, targetIdx) {
  const fromIdx = parseInt(evt.dataTransfer.getData('text/plain'), 10)
  if (fromIdx === targetIdx || isNaN(fromIdx)) return
  const items = [...conditions.value]
  const [moved] = items.splice(fromIdx, 1)
  items.splice(targetIdx, 0, moved)
  conditions.value = items
  draggingIdx.value = -1
}
</script>
```

- [ ] **Step 5: Build to verify**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | tail -5
```
Expected: `✓ built`

- [ ] **Step 6: Commit**

```bash
cd /opt/claude/hygie && git add frontend/vue/src/components/rules/
git commit -m "feat(rules): ExpertRuleBuilder with drag-and-drop cards + ConnectorPill + LogicRecap"
```

---

### Task 4: CreateRuleModal + full RulesView

**Files:**
- Create: `frontend/vue/src/components/rules/CreateRuleModal.vue`
- Modify: `frontend/vue/src/views/RulesView.vue`

- [ ] **Step 1: Create `CreateRuleModal.vue`**

```vue
<!-- frontend/vue/src/components/rules/CreateRuleModal.vue -->
<template>
  <!-- Backdrop -->
  <div class="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4" @click.self="$emit('close')">
    <div class="bg-[var(--bg2)] border border-[var(--border)] rounded-2xl w-full max-w-2xl max-h-[90vh] overflow-y-auto shadow-2xl">
      <!-- Header -->
      <div class="flex items-center justify-between px-6 py-4 border-b border-[var(--border)]">
        <h2 class="font-bold text-lg">
          {{ editMode ? 'Modifier la règle' : 'Nouvelle règle' }}
        </h2>
        <button @click="$emit('close')" class="text-[var(--muted)] hover:text-white transition-colors">
          <i class="fas fa-times" />
        </button>
      </div>

      <div class="p-6 space-y-6">
        <!-- Step 1: Type selector (only for new rules) -->
        <div v-if="!editMode">
          <p class="text-sm text-[var(--muted)] mb-3">Choisissez le type de règle :</p>
          <RuleTypeSelector :selected="ruleType" @select="ruleType = $event" />
        </div>

        <!-- Step 2: Form -->
        <div v-if="ruleType">
          <div class="h-px bg-[var(--border)] my-2" v-if="!editMode" />
          <SimpleRuleForm  v-if="ruleType === 'simple'"  v-model="formData" />
          <ExpertRuleBuilder v-if="ruleType === 'expert'" v-model="formData" />
        </div>

        <p v-if="error" class="text-red-400 text-sm">{{ error }}</p>
      </div>

      <!-- Footer -->
      <div class="flex items-center justify-end gap-3 px-6 py-4 border-t border-[var(--border)]">
        <button @click="$emit('close')" class="text-sm text-[var(--muted)] hover:text-white transition-colors px-4 py-2">
          Annuler
        </button>
        <button
          @click="submit"
          :disabled="!ruleType || saving"
          class="bg-[var(--accent)] hover:opacity-90 disabled:opacity-40 rounded-lg px-5 py-2 text-sm font-semibold transition-opacity"
        >
          {{ saving ? 'Enregistrement...' : (editMode ? 'Mettre à jour' : 'Créer la règle') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRulesStore } from '@/stores/rules'
import RuleTypeSelector  from './RuleTypeSelector.vue'
import SimpleRuleForm    from './SimpleRuleForm.vue'
import ExpertRuleBuilder from './ExpertRuleBuilder.vue'

const props = defineProps({
  initialType: { type: String, default: '' },
  initialData: { type: Object, default: () => ({}) },
  editMode:    { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'created'])

const rules    = useRulesStore()
const ruleType = ref(props.initialType || '')
const formData = reactive({ ...props.initialData })
const saving   = ref(false)
const error    = ref('')

async function submit() {
  if (!ruleType.value) return
  saving.value = true
  error.value  = ''
  try {
    if (ruleType.value === 'simple') {
      if (props.editMode && props.initialData.id) {
        await rules.updateSimpleRule(props.initialData.id, formData)
      } else {
        await rules.createSimpleRule(formData)
      }
    } else {
      // Serialize conditions (remove internal _key)
      const payload = {
        ...formData,
        conditions: (formData.conditions || []).map(({ _key, ...c }) => c),
      }
      if (props.editMode && props.initialData.id) {
        await rules.updateExpertRule(props.initialData.id, payload)
      } else {
        await rules.createExpertRule(payload)
      }
    }
    emit('created')
    emit('close')
  } catch (e) {
    error.value = e.response?.data?.detail || 'Erreur lors de l\'enregistrement'
  } finally {
    saving.value = false
  }
}
</script>
```

- [ ] **Step 2: Replace `frontend/vue/src/views/RulesView.vue` stub with full implementation**

```vue
<!-- frontend/vue/src/views/RulesView.vue -->
<template>
  <div class="space-y-5">
    <div class="flex items-center justify-between">
      <h2 class="font-semibold">Règles</h2>
      <button
        @click="showCreate = true"
        class="bg-[var(--accent)] hover:opacity-90 rounded-lg px-4 py-2 text-sm font-medium flex items-center gap-2 transition-opacity"
      >
        <i class="fas fa-plus" />
        Nouvelle règle
      </button>
    </div>

    <!-- Rule list -->
    <div v-if="rules.loading" class="text-[var(--muted)] text-sm p-8 text-center">Chargement...</div>

    <div v-else-if="!allRules.length" class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-12 text-center text-[var(--muted)] text-sm">
      Aucune règle. Créez votre première règle pour commencer la gestion automatique.
    </div>

    <div v-else class="space-y-2">
      <div
        v-for="rule in allRules"
        :key="`${rule._type}-${rule.id}`"
        class="flex items-center gap-4 bg-[var(--bg2)] border border-[var(--border)] rounded-xl px-5 py-3.5 hover:border-[var(--accent)]/30 transition-colors group"
      >
        <!-- Type badge -->
        <span
          class="text-[10px] font-bold uppercase tracking-widest px-2 py-0.5 rounded flex-shrink-0"
          :class="rule._type === 'expert'
            ? 'bg-purple-500/20 text-purple-400'
            : 'bg-[var(--accent)]/20 text-[var(--accent)]'"
        >
          {{ rule._type === 'expert' ? 'Expert' : 'Simple' }}
        </span>

        <!-- Name + summary -->
        <div class="flex-1 min-w-0">
          <div class="font-medium text-sm truncate">{{ rule.name }}</div>
          <div class="text-xs text-[var(--muted)] mt-0.5">
            <template v-if="rule._type === 'expert'">
              {{ rule.conditions?.length || 0 }} condition(s) ·
              {{ rule.operator }} · Action: {{ actionLabel(rule.action) }}
            </template>
            <template v-else>
              {{ rule.grace_days }} jours de grâce · {{ rule.watched_percent || 80 }}% visionné
            </template>
          </div>
        </div>

        <!-- Priority (expert only) -->
        <span v-if="rule._type === 'expert'" class="text-xs text-[var(--muted)] flex-shrink-0">
          P{{ rule.priority ?? 0 }}
        </span>

        <!-- Enabled toggle -->
        <ToggleSlider
          :model-value="!!rule.enabled"
          @update:model-value="toggleRule(rule)"
          class="flex-shrink-0"
        />

        <!-- Actions -->
        <div class="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
          <button @click="startEdit(rule)" class="text-[var(--muted)] hover:text-white transition-colors text-sm">
            <i class="fas fa-pen" />
          </button>
          <button @click="confirmDelete(rule)" class="text-[var(--muted)] hover:text-red-400 transition-colors text-sm">
            <i class="fas fa-trash" />
          </button>
        </div>
      </div>
    </div>

    <!-- Create modal -->
    <CreateRuleModal
      v-if="showCreate"
      @close="showCreate = false"
      @created="rules.fetchAll()"
    />

    <!-- Edit modal -->
    <CreateRuleModal
      v-if="editRule"
      :initial-type="editRule._type"
      :initial-data="editRule"
      :edit-mode="true"
      @close="editRule = null"
      @created="rules.fetchAll()"
    />

    <!-- Delete confirm -->
    <div v-if="deleteTarget"
      class="fixed inset-0 bg-black/60 z-50 flex items-center justify-center"
      @click.self="deleteTarget = null"
    >
      <div class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 w-80 space-y-4">
        <p class="text-sm">Supprimer la règle <strong>{{ deleteTarget.name }}</strong> ?</p>
        <div class="flex gap-3 justify-end">
          <button @click="deleteTarget = null" class="text-sm text-[var(--muted)] hover:text-white px-4 py-2">Annuler</button>
          <button @click="doDelete" class="bg-red-500 hover:bg-red-600 text-white text-sm rounded-lg px-4 py-2 transition-colors">Supprimer</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { useRulesStore }  from '@/stores/rules'
import ToggleSlider       from '@/components/ui/ToggleSlider.vue'
import CreateRuleModal    from '@/components/rules/CreateRuleModal.vue'

const rules = useRulesStore()

const showCreate  = ref(false)
const editRule    = ref(null)
const deleteTarget = ref(null)

const allRules = computed(() => [
  ...rules.simpleRules.map(r => ({ ...r, _type: 'simple' })),
  ...rules.expertRules.map(r => ({ ...r, _type: 'expert' })),
])

const ACTION_LABELS = { queue: 'Suppression', notify_only: 'Notification', ignore: 'Ignorer' }
const actionLabel = a => ACTION_LABELS[a] || a

async function toggleRule(rule) {
  if (rule._type === 'expert') {
    await rules.toggleExpertRule(rule.id)
  } else {
    await rules.updateSimpleRule(rule.id, { ...rule, enabled: !rule.enabled })
  }
}

function startEdit(rule) { editRule.value = { ...rule } }
function confirmDelete(rule) { deleteTarget.value = rule }

async function doDelete() {
  if (!deleteTarget.value) return
  const { _type, id } = deleteTarget.value
  if (_type === 'expert') await rules.deleteExpertRule(id)
  else await rules.deleteSimpleRule(id)
  deleteTarget.value = null
}

onMounted(rules.fetchAll)
</script>
```

- [ ] **Step 3: Build final**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | tail -5
```
Expected: `✓ built`

- [ ] **Step 4: Build Docker image to test end-to-end**

```bash
cd /opt/claude/hygie && docker build -t hygie:rules-test .
docker run --rm -d -p 8002:8000 --name hygie-rules-test hygie:rules-test
sleep 3
curl -s -o /dev/null -w "%{http_code}" http://localhost:8002/
docker stop hygie-rules-test
```
Expected: `200`

- [ ] **Step 5: Commit**

```bash
cd /opt/claude/hygie && git add frontend/vue/src/
git commit -m "feat(rules): CreateRuleModal with type selector + full RulesView (simple + expert)"
```

---

### Task 5: Version bump + tag v3.0.0-alpha.4

- [ ] **Step 1: Bump version**

```python
VERSION = "3.0.0-alpha.4"
```

- [ ] **Step 2: Run backend tests + Docker build**

```bash
cd /opt/claude/hygie && python -m pytest tests/ -q 2>&1 | tail -5
docker build -t hygie:3.0.0-alpha.4 .
```

- [ ] **Step 3: Tag and push**

```bash
git add backend/version.py
git commit -m "chore: bump version to 3.0.0-alpha.4 (unified rule builder)"
git tag v3.0.0-alpha.4
git push origin main --tags
```
