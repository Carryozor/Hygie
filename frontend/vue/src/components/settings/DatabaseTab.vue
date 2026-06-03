<template>
  <div class="space-y-6">

    <!-- Current DB info -->
    <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-4">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-lg bg-[var(--accent)]/20 flex items-center justify-center">
            <i class="fas fa-database text-[var(--accent)]" />
          </div>
          <h2 class="font-semibold">{{ t('settings.database.title') }}</h2>
        </div>
        <button
          class="px-3 py-1.5 text-xs rounded-lg bg-[var(--bg3)] border border-[var(--border)] text-[var(--muted)] hover:text-white transition-colors flex items-center gap-1.5"
          :disabled="loadingInfo"
          @click="fetchInfo">
          <i :class="['fas', loadingInfo ? 'fa-spinner fa-spin' : 'fa-rotate-right', 'text-xs']" />
          {{ t('settings.database.refresh') }}
        </button>
      </div>

      <div v-if="info" class="space-y-3">
        <!-- Type + connection -->
        <div class="grid grid-cols-2 gap-3">
          <div class="bg-[var(--bg3)] rounded-lg px-4 py-3">
            <div class="text-xs text-[var(--muted)] mb-1">{{ t('settings.database.type') }}</div>
            <div class="flex items-center gap-2">
              <span
                class="text-xs px-2 py-0.5 rounded-full font-medium"
                :class="info.dialect === 'mariadb' ? 'bg-blue-500/20 text-blue-400' : 'bg-green-500/20 text-green-400'">
                {{ info.dialect === 'mariadb' ? 'MariaDB' : 'SQLite' /* proper names, no translation needed */ }}
              </span>
            </div>
          </div>
          <div class="bg-[var(--bg3)] rounded-lg px-4 py-3 min-w-0">
            <div class="text-xs text-[var(--muted)] mb-1">{{ t('settings.database.connection') }}</div>
            <div class="text-xs font-mono text-[var(--text)] truncate" :title="info.connection">{{ info.connection }}</div>
          </div>
        </div>

        <!-- Table row counts -->
        <div class="bg-[var(--bg3)] rounded-lg p-3">
          <div class="text-xs text-[var(--muted)] mb-2">{{ t('settings.database.tables') }}</div>
          <div class="grid grid-cols-2 sm:grid-cols-3 gap-1.5">
            <div
              v-for="(count, table) in info.tables" :key="table"
              class="flex items-center justify-between bg-[var(--bg2)] rounded px-2 py-1">
              <span class="text-xs font-mono text-[var(--muted)] truncate">{{ table }}</span>
              <span class="text-xs text-[var(--text)] ml-2 flex-shrink-0">{{ count >= 0 ? count : '?' }}</span>
            </div>
          </div>
        </div>
      </div>
      <div v-else-if="loadingInfo" class="text-center py-6 text-[var(--muted)]">
        <i class="fas fa-spinner fa-spin" />
      </div>
    </section>

    <!-- Migration -->
    <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-5">
      <h2 class="font-semibold text-sm">{{ t('settings.database.migrate') }}</h2>

      <!-- Direction -->
      <div>
        <label class="block text-xs text-[var(--muted)] mb-2">{{ t('settings.database.direction') }}</label>
        <div class="flex gap-2">
          <button
            v-for="opt in directionOptions" :key="opt.value"
            class="flex-1 px-3 py-2 rounded-lg text-sm border transition-colors"
            :class="direction === opt.value
              ? 'bg-[var(--accent)]/20 border-[var(--accent)]/50 text-[var(--accent)]'
              : 'bg-[var(--bg3)] border-[var(--border)] text-[var(--muted)] hover:text-white'"
            :disabled="migrating || !opt.available"
            :title="!opt.available ? opt.unavailableReason : ''"
            @click="direction = opt.value; resetStatus()">
            <span :class="{ 'opacity-40': !opt.available }">{{ opt.label }}</span>
          </button>
        </div>
      </div>

      <!-- Target URL (for sqlite_to_mariadb) -->
      <div v-if="direction === 'sqlite_to_mariadb'">
        <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.database.targetUrl') }}</label>
        <div class="flex gap-2">
          <input
            v-model="targetUrl"
            type="text"
            :placeholder="t('settings.database.urlPlaceholder')"
            class="flex-1 field font-mono text-xs"
            @input="testResult = null" />
          <button
            class="px-3 py-2 border border-[var(--border)] rounded-lg text-xs text-[var(--muted)] hover:text-white transition-colors flex items-center gap-1.5"
            :disabled="!targetUrl || testing"
            @click="testConnection">
            <i :class="['fas', testing ? 'fa-spinner fa-spin' : 'fa-plug', 'text-xs']" />
            {{ t('settings.database.testConn') }}
          </button>
        </div>
        <div
          v-if="testResult !== null"
          class="mt-1.5 text-xs px-3 py-1.5 rounded-lg"
          :class="testResult.ok ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'">
          <i :class="['fas', testResult.ok ? 'fa-circle-check' : 'fa-circle-exclamation', 'mr-1']" />
          {{ testResult.message }}
        </div>
      </div>

      <!-- Target path (for mariadb_to_sqlite) -->
      <div v-if="direction === 'mariadb_to_sqlite'">
        <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.database.targetPath') }}</label>
        <input
          v-model="targetPath"
          type="text"
          :placeholder="t('settings.database.pathPlaceholder')"
          class="field font-mono text-xs" />
      </div>

      <!-- Dry run -->
      <label class="flex items-center gap-3 cursor-pointer">
        <input v-model="dryRun" type="checkbox" class="rounded" />
        <div>
          <div class="text-sm">{{ t('settings.database.dryRun') }}</div>
          <div class="text-xs text-[var(--muted)]">{{ t('settings.database.dryRunHint') }}</div>
        </div>
      </label>

      <!-- Action button -->
      <button
        class="w-full bg-[var(--accent)] hover:opacity-90 disabled:opacity-40 rounded-lg px-4 py-2.5 text-sm font-semibold transition-opacity"
        :disabled="migrating || !canMigrate"
        @click="startMigration">
        <i v-if="migrating" class="fas fa-spinner fa-spin mr-2" />
        {{ migrating ? t('settings.database.migrating') : t('settings.database.startMigration') }}
      </button>

      <!-- Status -->
      <div v-if="jobStatus" class="space-y-2">
        <div
          class="flex items-start gap-3 rounded-lg px-4 py-3 text-sm"
          :class="{
            'bg-green-500/10 border border-green-500/30 text-green-400': jobStatus.status === 'success',
            'bg-red-500/10 border border-red-500/30 text-red-400': jobStatus.status === 'error',
            'bg-[var(--bg3)] border border-[var(--border)] text-[var(--muted)]': !jobStatus.status,
          }">
          <i :class="['fas mt-0.5', jobStatus.status === 'success' ? 'fa-circle-check text-green-400' : jobStatus.status === 'error' ? 'fa-circle-xmark text-red-400' : 'fa-spinner fa-spin']" />
          <div>
            <div class="font-medium">
              {{ jobStatus.status === 'success' ? t('settings.database.success') : jobStatus.status === 'error' ? t('settings.database.error') : t('settings.database.migrating') }}
            </div>
            <div v-if="jobStatus.message" class="text-xs mt-0.5 opacity-80 font-mono break-all">{{ jobStatus.message }}</div>
          </div>
        </div>

        <!-- Restart instruction after successful migration -->
        <div
          v-if="jobStatus.status === 'success' && !dryRun && direction === 'sqlite_to_mariadb'"
          class="bg-yellow-500/10 border border-yellow-500/30 text-yellow-300 rounded-lg px-4 py-3 text-xs space-y-1">
          <div class="font-semibold flex items-center gap-1.5">
            <i class="fas fa-triangle-exclamation" /> {{ t('settings.database.restartHint') }} :
          </div>
          <code class="block bg-black/30 rounded px-2 py-1 font-mono break-all">DATABASE_URL={{ targetUrl }}</code>
          <div class="opacity-70">{{ t('settings.database.restartEnvHint') }}</div>
        </div>
        <div
          v-else-if="jobStatus.status === 'success' && !dryRun && direction === 'mariadb_to_sqlite'"
          class="bg-yellow-500/10 border border-yellow-500/30 text-yellow-300 rounded-lg px-4 py-3 text-xs space-y-1">
          <div class="font-semibold flex items-center gap-1.5">
            <i class="fas fa-triangle-exclamation" /> {{ t('settings.database.restartHint') }} :
          </div>
          <code class="block bg-black/30 rounded px-2 py-1 font-mono break-all">DATABASE_URL= (vide) et DB_PATH={{ targetPath || '/app/data/hygie_migrated.db' }}</code>
          <div class="opacity-70">{{ t('settings.database.restartEnvHint') }}</div>
        </div>
      </div>
    </section>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import api from '@/api/client'

const { t } = useI18n()

const info        = ref(null)
const loadingInfo = ref(false)
const direction   = ref('sqlite_to_mariadb')
const targetUrl   = ref('')
const targetPath  = ref('/app/data/hygie_migrated.db')
const dryRun      = ref(false)
const testing     = ref(false)
const testResult  = ref(null)
const migrating   = ref(false)
const jobStatus   = ref(null)
let   pollTimer   = null

const directionOptions = computed(() => [
  {
    value: 'sqlite_to_mariadb',
    label: t('settings.database.toMariadb'),
    available: info.value?.dialect === 'sqlite',
    unavailableReason: info.value?.dialect === 'mariadb' ? t('settings.database.alreadyMariadb', 'Already on MariaDB') : '',
  },
  {
    value: 'mariadb_to_sqlite',
    label: t('settings.database.toSqlite'),
    available: info.value?.dialect === 'mariadb',
    unavailableReason: info.value?.dialect === 'sqlite' ? t('settings.database.alreadySqlite', 'Already on SQLite') : '',
  },
])

const canMigrate = computed(() => {
  if (direction.value === 'sqlite_to_mariadb') return !!targetUrl.value.trim()
  if (direction.value === 'mariadb_to_sqlite') return true
  return false
})

async function fetchInfo() {
  loadingInfo.value = true
  try {
    const { data } = await api.get('/database/info')
    info.value = data
    // Auto-select available direction
    if (data.dialect === 'mariadb') direction.value = 'mariadb_to_sqlite'
  } catch { /* silent */ }
  finally { loadingInfo.value = false }
}

async function testConnection() {
  testing.value = true
  testResult.value = null
  try {
    const { data } = await api.post('/database/test', { url: targetUrl.value })
    testResult.value = data
  } catch (e) {
    testResult.value = { ok: false, message: e?.response?.data?.detail || t('settings.database.testFail') }
  } finally { testing.value = false }
}

function resetStatus() {
  jobStatus.value = null
  testResult.value = null
}

async function fetchJobStatus() {
  try {
    const { data } = await api.get('/database/migrate/status')
    jobStatus.value = data
    if (data && !data.status) {
      migrating.value = true
    } else {
      migrating.value = false
      stopPolling()
    }
  } catch { /* silent */ }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(fetchJobStatus, 2000)
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

async function startMigration() {
  if (!canMigrate.value || migrating.value) return
  jobStatus.value = null
  migrating.value = true
  try {
    const payload = {
      direction: direction.value,
      target_url:  direction.value === 'sqlite_to_mariadb' ? targetUrl.value : undefined,
      target_path: direction.value === 'mariadb_to_sqlite' ? (targetPath.value || undefined) : undefined,
      dry_run: dryRun.value,
    }
    const { data } = await api.post('/database/migrate', payload)
    if (data.status === 'already_running') {
      window.dispatchEvent(new CustomEvent('hygie:error', { detail: { message: t('settings.database.alreadyRunning') } }))
      migrating.value = false
      return
    }
    startPolling()
  } catch (e) {
    migrating.value = false
    jobStatus.value = { status: 'error', message: e?.response?.data?.error || e?.message || t('common.error') }
  }
}

onMounted(async () => {
  await fetchInfo()
  await fetchJobStatus()
  if (migrating.value) startPolling()
})

onUnmounted(() => stopPolling())
</script>

<style scoped>
.field { @apply w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]; }
</style>
