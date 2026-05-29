<!-- frontend/vue/src/views/RulesView.vue -->
<template>
  <div class="space-y-6">
    <!-- Header -->
    <div class="flex items-center justify-between">
      <h2 class="font-semibold text-lg">Règles</h2>
      <button
        class="bg-[var(--accent)] hover:opacity-90 rounded-lg px-4 py-2 text-sm font-medium transition-opacity flex items-center gap-2"
        @click="openCreate"
      >
        <i class="fas fa-plus text-xs" />
        Nouvelle règle
      </button>
    </div>

    <!-- Loading -->
    <div v-if="rules.loading" class="text-center text-[var(--muted)] py-12">
      <i class="fas fa-spinner fa-spin text-2xl" />
    </div>

    <template v-else>
      <!-- Simple rules -->
      <section>
        <h3 class="text-xs text-[var(--muted)] uppercase tracking-wide mb-3">Règles simples (Seerr)</h3>
        <div v-if="!rules.simpleRules.length" class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 text-center text-[var(--muted)] text-sm">
          Aucune règle simple.
        </div>
        <div v-else class="space-y-2">
          <div
            v-for="r in rules.simpleRules"
            :key="r.id"
            class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl px-4 py-3 flex items-center gap-3"
          >
            <i class="fas fa-user-tag text-[var(--accent)]" />
            <div class="flex-1 min-w-0">
              <div class="text-sm font-medium truncate">{{ r.seerr_username || '—' }}</div>
              <div class="text-xs text-[var(--muted)]">
                Bibliothèque: {{ libraryName(r.library_id) }} · {{ r.grace_days }}j de grâce
                <span v-if="r.discord_id"> · Discord: {{ r.discord_id }}</span>
              </div>
            </div>
            <!-- Enabled badge -->
            <span :class="['text-xs px-2 py-0.5 rounded-full', r.enabled ? 'bg-green-500/20 text-green-400' : 'bg-[var(--border)] text-[var(--muted)]']">
              {{ r.enabled ? 'Actif' : 'Inactif' }}
            </span>
            <!-- Actions -->
            <button class="text-[var(--muted)] hover:text-[var(--text)] transition-colors" @click="editSimple(r)">
              <i class="fas fa-pencil text-sm" />
            </button>
            <button class="text-[var(--muted)] hover:text-red-400 transition-colors" @click="confirmDelete('simple', r.id)">
              <i class="fas fa-trash text-sm" />
            </button>
          </div>
        </div>
      </section>

      <!-- Expert rules -->
      <section>
        <h3 class="text-xs text-[var(--muted)] uppercase tracking-wide mb-3">Règles expertes</h3>
        <div v-if="!rules.expertRules.length" class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 text-center text-[var(--muted)] text-sm">
          Aucune règle experte.
        </div>
        <div v-else class="space-y-2">
          <div
            v-for="r in rules.expertRules"
            :key="r.id"
            class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl px-4 py-3 flex items-center gap-3"
          >
            <i class="fas fa-sliders text-[var(--accent)]" />
            <div class="flex-1 min-w-0">
              <div class="text-sm font-medium truncate">{{ r.name }}</div>
              <div class="text-xs text-[var(--muted)]">
                {{ r.conditions?.length ?? 0 }} condition(s) ·
                {{ r.operator }} ·
                {{ ACTION_LABELS[r.action] ?? r.action }}
                <template v-if="r.library_id"> · {{ libraryName(r.library_id) }}</template>
              </div>
            </div>
            <!-- Toggle enabled -->
            <button
              :class="['text-xs px-2 py-0.5 rounded-full transition-colors', r.enabled ? 'bg-green-500/20 text-green-400' : 'bg-[var(--border)] text-[var(--muted)]']"
              @click="rules.toggleExpertRule(r.id)"
            >
              {{ r.enabled ? 'Actif' : 'Inactif' }}
            </button>
            <button class="text-[var(--muted)] hover:text-[var(--text)] transition-colors" @click="editExpert(r)">
              <i class="fas fa-pencil text-sm" />
            </button>
            <button class="text-[var(--muted)] hover:text-red-400 transition-colors" @click="confirmDelete('expert', r.id)">
              <i class="fas fa-trash text-sm" />
            </button>
          </div>
        </div>
      </section>
    </template>

    <!-- Delete confirmation -->
    <Teleport to="body">
      <div
        v-if="deleteTarget"
        class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
        @mousedown.self="deleteTarget = null"
      >
        <div class="bg-[var(--bg1)] border border-[var(--border)] rounded-2xl p-6 w-full max-w-sm shadow-2xl space-y-4">
          <p class="text-sm">Supprimer cette règle définitivement ?</p>
          <div class="flex justify-end gap-3">
            <button class="px-4 py-2 text-sm text-[var(--muted)] hover:text-[var(--text)]" @click="deleteTarget = null">Annuler</button>
            <button class="px-4 py-2 bg-red-500 hover:bg-red-600 text-white text-sm rounded-lg transition-colors" @click="doDelete">Supprimer</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Create / Edit modal -->
    <CreateRuleModal
      :open="modal.open"
      :edit-rule="modal.rule"
      :edit-type="modal.type"
      @close="modal.open = false"
      @saved="onSaved"
    />
  </div>
</template>

<script setup>
import { reactive, computed, onMounted, ref } from 'vue'
import { useRulesStore } from '@/stores/rules'
import { useServersStore } from '@/stores/servers'
import CreateRuleModal from '@/components/rules/CreateRuleModal.vue'

const rules   = useRulesStore()
const servers = useServersStore()

const ACTION_LABELS = { queue: 'File de suppression', notify_only: 'Notification seulement' }

const modal = reactive({ open: false, rule: null, type: '' })
const deleteTarget = ref(null)

const libraryName = computed(() => (id) => {
  const lib = servers.libraries.find(l => String(l.id) === String(id))
  return lib ? lib.name : id || 'Toutes'
})

function openCreate() {
  modal.rule = null
  modal.type = ''
  modal.open = true
}

function editSimple(r) {
  modal.rule = { ...r }
  modal.type = 'simple'
  modal.open = true
}

function editExpert(r) {
  modal.rule = { ...r, conditions: r.conditions ? JSON.parse(JSON.stringify(r.conditions)) : [] }
  modal.type = 'expert'
  modal.open = true
}

function confirmDelete(type, id) {
  deleteTarget.value = { type, id }
}

async function doDelete() {
  if (!deleteTarget.value) return
  const { type, id } = deleteTarget.value
  deleteTarget.value = null
  if (type === 'simple') await rules.deleteSimpleRule(id)
  else await rules.deleteExpertRule(id)
}

async function onSaved({ type, data }) {
  if (type === 'simple') {
    if (modal.rule?.id) await rules.updateSimpleRule(modal.rule.id, data)
    else await rules.createSimpleRule(data)
  } else {
    if (modal.rule?.id) await rules.updateExpertRule(modal.rule.id, data)
    else await rules.createExpertRule(data)
  }
  modal.open = false
}

onMounted(async () => {
  await Promise.all([
    rules.fetchAll(),
    servers.libraries.length ? Promise.resolve() : servers.fetch(),
  ])
})
</script>
