<!-- frontend/vue/src/views/RulesView.vue -->
<template>
  <div class="space-y-6">
    <!-- Header -->
    <div class="flex items-center justify-between">
      <h2 class="font-semibold text-lg">{{ t('rules.title') }}</h2>
      <button
        class="bg-[var(--accent)] hover:opacity-90 rounded-lg px-4 py-2 text-sm font-medium transition-opacity flex items-center gap-2"
        @click="openCreate"
      >
        <i class="fas fa-plus text-xs" />
        {{ t('rules.new') }}
      </button>
    </div>

    <!-- Loading -->
    <div v-if="rules.loading" class="text-center text-[var(--muted)] py-12">
      <i class="fas fa-spinner fa-spin text-2xl" />
    </div>

    <template v-else>
      <!-- Simple rules -->
      <section>
        <h3 class="text-xs text-[var(--muted)] uppercase tracking-wide mb-3">{{ t('rules.simpleSection.title') }}</h3>
        <div v-if="!rules.simpleRules.length" class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 text-center text-[var(--muted)] text-sm">
          {{ t('rules.simpleSection.empty') }}
        </div>
        <div v-else class="space-y-2">
          <div
            v-for="r in rules.simpleRules"
            :key="r.id"
            class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl px-4 py-3 flex items-center gap-3"
          >
            <i class="fas fa-user-tag text-[var(--accent)]" />
            <div class="flex-1 min-w-0">
              <div class="text-sm font-medium truncate">{{ r.name || r.seerr_username || '—' }}</div>
              <div class="text-xs text-[var(--muted)]">
                {{ r.seerr_username }} · {{ libraryName(r.library_id) }} · {{ r.grace_days }}j de grâce
                <span v-if="r.discord_id"> · Discord: {{ r.discord_id }}</span>
              </div>
            </div>
            <!-- Enabled toggle -->
            <button
              :class="['text-xs px-2 py-0.5 rounded-full transition-colors', r.enabled ? 'bg-green-500/20 text-green-400' : 'bg-[var(--border)] text-[var(--muted)]']"
              :title="r.enabled ? t('rules.disable') : t('rules.enable')"
              @click="toggleSimple(r)"
            >
              {{ r.enabled ? t('common.enabled') : t('common.disabled') }}
            </button>
            <!-- Run -->
            <button class="text-[var(--muted)] hover:text-green-400 transition-colors" :title="t('rules.runScan')" :disabled="runningId === (r.library_id || 'all')" @click="runRule(r.library_id)">
              <i :class="['fas', runningId === (r.library_id || 'all') ? 'fa-spinner fa-spin' : 'fa-play', 'text-sm']" />
            </button>
            <!-- Edit -->
            <button class="text-[var(--muted)] hover:text-[var(--text)] transition-colors" :title="t('common.edit')" @click="editSimple(r)">
              <i class="fas fa-pencil text-sm" />
            </button>
            <!-- Clone -->
            <button class="text-[var(--muted)] hover:text-[var(--accent)] transition-colors" :title="t('common.clone', 'Cloner')" @click="cloneSimple(r)">
              <i class="fas fa-copy text-sm" />
            </button>
            <!-- Delete -->
            <button class="text-[var(--muted)] hover:text-red-400 transition-colors" :title="t('common.delete')" @click="confirmDelete('simple', r.id)">
              <i class="fas fa-trash text-sm" />
            </button>
          </div>
        </div>
      </section>

      <!-- Expert rules -->
      <section>
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-xs text-[var(--muted)] uppercase tracking-wide">{{ t('rules.expertSection.title') }}</h3>
          <button
            class="text-xs px-3 py-1.5 rounded-lg bg-[var(--bg2)] border border-[var(--border)] hover:bg-[var(--bg3)] text-[var(--muted)] hover:text-[var(--text)] transition-colors flex items-center gap-1.5"
            :disabled="migrating"
            :title="t('rules.expertSection.migrateTooltip')"
            @click="migrateFromLibraries"
          >
            <i :class="['fas', migrating ? 'fa-spinner fa-spin' : 'fa-wand-magic-sparkles', 'text-[10px]']" />
            {{ migrating ? t('rules.expertSection.migrating') : t('rules.expertSection.migrate') }}
          </button>
        </div>
        <div v-if="migrateMsg" class="mb-2 text-xs px-3 py-2 rounded-lg" :class="migrateMsg.ok ? 'bg-green-500/10 text-green-400 border border-green-500/30' : 'bg-red-500/10 text-red-400 border border-red-500/30'">
          {{ migrateMsg.text }}
        </div>
        <div v-if="!rules.expertRules.length" class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 text-center text-[var(--muted)] text-sm">
          {{ t('rules.expertSection.empty') }}
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
                {{ totalConditions(r) }} {{ t('rules.conditions') }} · {{ r.condition_groups?.length ?? 1 }} {{ t('rules.blocks') }} ·
                {{ r.operator }} ·
                {{ actionLabel(r.action) }} ·
                {{ r.grace_days ?? 7 }}j de grâce
                <template v-if="r.library_id"> · {{ libraryName(r.library_id) }}</template>
              </div>
            </div>
            <!-- Toggle enabled -->
            <button
              :class="['text-xs px-2 py-0.5 rounded-full transition-colors', r.enabled ? 'bg-green-500/20 text-green-400' : 'bg-[var(--border)] text-[var(--muted)]']"
              @click="rules.toggleExpertRule(r.id)"
            >
              {{ r.enabled ? t('common.enabled') : t('common.disabled') }}
            </button>
            <!-- Run -->
            <button class="text-[var(--muted)] hover:text-green-400 transition-colors" :title="t('rules.runScan')" :disabled="runningId === (r.library_id || 'all')" @click="runRule(r.library_id)">
              <i :class="['fas', runningId === (r.library_id || 'all') ? 'fa-spinner fa-spin' : 'fa-play', 'text-sm']" />
            </button>
            <!-- Edit -->
            <button class="text-[var(--muted)] hover:text-[var(--text)] transition-colors" :title="t('common.edit')" @click="editExpert(r)">
              <i class="fas fa-pencil text-sm" />
            </button>
            <!-- Clone -->
            <button class="text-[var(--muted)] hover:text-[var(--accent)] transition-colors" :title="t('common.clone', 'Cloner')" @click="cloneExpert(r)">
              <i class="fas fa-copy text-sm" />
            </button>
            <!-- Delete -->
            <button class="text-[var(--muted)] hover:text-red-400 transition-colors" :title="t('common.delete')" @click="confirmDelete('expert', r.id)">
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
          <p class="text-sm">{{ t('rules.confirmDelete') }}</p>
          <div class="flex justify-end gap-3">
            <button class="px-4 py-2 text-sm text-[var(--muted)] hover:text-[var(--text)]" @click="deleteTarget = null">{{ t('common.cancel') }}</button>
            <button class="px-4 py-2 bg-red-500 hover:bg-red-600 text-white text-sm rounded-lg transition-colors" @click="doDelete">{{ t('common.delete') }}</button>
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
import { useI18n } from 'vue-i18n'
import { useRulesStore } from '@/stores/rules'
import { useServersStore } from '@/stores/servers'
import CreateRuleModal from '@/components/rules/CreateRuleModal.vue'
import api from '@/api/client'

const { t } = useI18n()
const rules   = useRulesStore()
const servers = useServersStore()

const migrating  = ref(false)
const migrateMsg = ref(null)
const runningId  = ref(null)

async function runRule(libraryId) {
  const key = libraryId || 'all'
  runningId.value = key
  try {
    if (libraryId) await api.post(`/libraries/${libraryId}/scan`)
    else await api.post('/scan/trigger')
  } catch { /* silent — scan may already be running */ }
  finally { runningId.value = null }
}

async function migrateFromLibraries() {
  migrating.value = true
  migrateMsg.value = null
  try {
    const { data } = await api.post('/expert-rules/migrate-from-libraries')
    const n = data.created ?? 0
    migrateMsg.value = {
      ok: true,
      text: n === 0
        ? t('rules.expertSection.migrateNone')
        : t('rules.expertSection.migrateSuccess', { n }),
    }
    if (n > 0) await rules.fetchAll()
  } catch {
    migrateMsg.value = { ok: false, text: t('rules.expertSection.migrateError') }
  } finally {
    migrating.value = false
    setTimeout(() => { migrateMsg.value = null }, 5000)
  }
}

function actionLabel(action) {
  if (action === 'queue') return t('rules.action.queue')
  if (action === 'notify_only') return t('rules.action.notifyOnly')
  return action
}

function totalConditions(r) {
  if (r.condition_groups?.length) {
    return r.condition_groups.reduce((s, g) => s + (g.conditions?.length ?? 0), 0)
  }
  return r.conditions?.length ?? 0
}

const modal = reactive({ open: false, rule: null, type: '' })
const deleteTarget = ref(null)

const libraryName = computed(() => (id) => {
  const lib = servers.libraries.find(l => String(l.id) === String(id))
  return lib ? lib.name : id || t('rules.allLibraries')
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

function cloneSimple(r) {
  const { id, ...rest } = r
  modal.rule = { ...rest }
  modal.type = 'simple'
  modal.open = true
}

function editExpert(r) {
  modal.rule = { ...r, conditions: r.conditions ? JSON.parse(JSON.stringify(r.conditions)) : [] }
  modal.type = 'expert'
  modal.open = true
}

function cloneExpert(r) {
  const { id, ...rest } = r
  modal.rule = {
    ...rest,
    name: `${rest.name} (copie)`,
    conditions: rest.conditions ? JSON.parse(JSON.stringify(rest.conditions)) : [],
  }
  modal.type = 'expert'
  modal.open = true
}

async function toggleSimple(r) {
  await rules.updateSimpleRule(r.id, { ...r, enabled: !r.enabled })
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
