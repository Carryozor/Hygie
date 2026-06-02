<template>
  <aside class="w-60 flex-shrink-0 flex flex-col border-r border-[var(--border)] bg-[var(--bg2)] h-screen sticky top-0 overflow-y-auto">
    <!-- Logo -->
    <div class="flex items-center gap-3 px-4 py-5 border-b border-[var(--border)]">
      <HygieLogoSvg :size="32" :has-error="hasUnseenErrors" :status-dot="logoStatus" :server-results="status.serverResults" />
      <div>
        <span class="font-bold text-lg tracking-tight">Hygie</span>
        <div class="text-[10px] text-[var(--muted)] leading-none">v{{ version }}</div>
      </div>
    </div>

    <!-- Nav links -->
    <nav class="flex-1 px-2 py-4 space-y-1">
      <router-link
        v-for="item in navItems"
        :key="item.to"
        :to="item.to"
        class="flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-[var(--muted)] hover:text-white hover:bg-[var(--bg3)] transition-colors"
        active-class="bg-[var(--bg3)] text-white"
      >
        <i :class="['fas', item.icon, 'w-4 text-center']" />
        <span>{{ item.label }}</span>
      </router-link>

      <div class="pt-4 pb-1 px-3">
        <span class="text-xs font-semibold uppercase tracking-widest text-[var(--muted)]">{{ t('nav.libraries') }}</span>
      </div>
      <ServerLibraryTree />
    </nav>

    <!-- Bottom: dry run + progress bars -->
    <div class="px-3 py-4 border-t border-[var(--border)] space-y-3">
      <!-- Dry Run toggle button -->
      <button
        :disabled="togglingDryRun"
        class="w-full flex items-center gap-2 px-3 py-2 rounded-lg border text-xs font-medium transition-colors"
        :class="isDryRun
          ? 'bg-yellow-500/10 border-yellow-500/30 text-yellow-400 hover:bg-yellow-500/20'
          : 'bg-[var(--bg3)] border-[var(--border)] text-[var(--muted)] hover:text-white hover:border-yellow-500/30'"
        @click="toggleDryRun"
      >
        <i class="fas fa-flask-vial w-4 text-center" />
        <span>{{ isDryRun ? t('sidebar.dryRunActive') : t('sidebar.dryRun') }}</span>
        <i v-if="togglingDryRun" class="fas fa-spinner fa-spin ml-auto text-[10px]" />
        <i v-else-if="isDryRun" class="fas fa-circle text-[8px] ml-auto text-yellow-400" />
      </button>

      <!-- Scan progress -->
      <div v-if="scanNext || scanRunning" class="space-y-1.5">
        <button
          type="button"
          class="w-full flex items-center justify-between text-xs text-[var(--muted)] hover:text-white transition-colors group"
          :disabled="triggering === 'scan' || scanRunning"
          @click="triggerScan"
        >
          <div class="flex items-center gap-1.5">
            <i :class="['fas', (triggering === 'scan' || scanRunning) ? 'fa-spinner fa-spin' : 'fa-magnifying-glass', 'w-4 text-center opacity-70']" />
            <span v-if="scanRunning || triggering === 'scan'" class="flex items-center gap-0.5">
              {{ triggering === 'scan' ? t('sidebar.launching') : t('sidebar.scanRunning') }}
            </span>
            <span v-else>{{ t('sidebar.nextScan') }}</span>
          </div>
          <span v-if="!scanRunning" class="font-mono text-[10px]">{{ scanCountdown }}</span>
        </button>
        <div class="h-2 bg-[var(--bg3)] rounded-full overflow-hidden">
          <div
            v-if="scanRunning"
            class="h-full rounded-full animate-scan-running"
          />
          <div
            v-else
            class="h-full rounded-full transition-all duration-1000"
            :style="{ width: scanProgress + '%', background: 'var(--accent)' }"
          />
        </div>
      </div>

      <!-- Deletion progress -->
      <div v-if="deletionNext || deletionRunning" class="space-y-1.5">
        <button
          type="button"
          class="w-full flex items-center justify-between text-xs text-[var(--muted)] hover:text-white transition-colors"
          :disabled="triggering === 'deletion' || deletionRunning"
          @click="triggerDeletion"
        >
          <div class="flex items-center gap-1.5">
            <i class="fas fa-trash-can w-4 text-center opacity-70" />
            <span v-if="deletionRunning || triggering === 'deletion'" class="flex items-center gap-0.5">
              {{ triggering === 'deletion' ? t('sidebar.launching') : t('sidebar.deletionRunning') }}
            </span>
            <span v-else>{{ t('sidebar.nextDeletion') }}</span>
          </div>
          <span v-if="!deletionRunning" class="font-mono text-[10px]">{{ deletionCountdown }}</span>
        </button>
        <div class="h-2 bg-[var(--bg3)] rounded-full overflow-hidden">
          <div
            v-if="deletionRunning"
            class="h-full rounded-full animate-deletion-running"
          />
          <div
            v-else
            class="h-full rounded-full transition-all duration-1000"
            :style="{ width: deletionProgress + '%', background: '#ef4444' }"
          />
        </div>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useSettingsStore } from '@/stores/settings'
import { useStatusStore } from '@/stores/status'
import HygieLogoSvg from '@/components/ui/HygieLogoSvg.vue'
import ServerLibraryTree from './ServerLibraryTree.vue'
import api from '@/api/client'

const { t } = useI18n()

const settings = useSettingsStore()
const status   = useStatusStore()

const version         = ref('')
const now             = ref(new Date())
const togglingDryRun  = ref(false)
const triggering      = ref(null) // 'scan' | 'deletion' | null
let   clockInterval   = null

// ── Store-backed computed refs ────────────────────────────────────────────────
const scanNext        = computed(() => status.scanNext)
const deletionNext    = computed(() => status.deletionNext)
const scanRunning     = computed(() => status.scanRunning)
const deletionRunning = computed(() => status.deletionRunning)
const hasUnseenErrors = computed(() => status.hasUnseenErrors)
const logoStatus      = computed(() => status.logoStatus)

const isDryRun = computed(() => settings.settings.dry_run === 'true' || settings.settings.dry_run === true)

async function triggerScan() {
  if (triggering.value) return
  triggering.value = 'scan'
  try { await api.post('/scan/trigger') } catch { /* silent — may be already running */ }
  finally { triggering.value = null; await status.fetchScheduler() }
}

async function triggerDeletion() {
  if (triggering.value) return
  triggering.value = 'deletion'
  try { await api.post('/deletion/trigger') } catch { /* silent */ }
  finally { triggering.value = null; await status.fetchScheduler() }
}

async function toggleDryRun() {
  togglingDryRun.value = true
  try {
    const next = isDryRun.value ? 'false' : 'true'
    await api.post('/settings', { dry_run: next })
    await settings.fetch()
  } catch { /* silent */ }
  finally { togglingDryRun.value = false }
}

const navItems = computed(() => [
  { to: '/',         icon: 'fa-chart-bar', label: t('nav.dashboard') },
  { to: '/queue',    icon: 'fa-list',      label: t('nav.queue') },
  { to: '/calendar', icon: 'fa-calendar',  label: t('nav.calendar') },
  { to: '/rules',    icon: 'fa-sliders-h', label: t('nav.rules') },
  { to: '/ignored',  icon: 'fa-ban',       label: t('nav.ignored') },
  { to: '/logs',     icon: 'fa-scroll',    label: t('nav.logs') },
  { to: '/settings', icon: 'fa-cog',       label: t('settings.title') },
])

function formatCountdown(isoDate) {
  if (!isoDate) return null
  const diff = new Date(isoDate) - now.value
  if (diff <= 0) return t('days.imminent')
  const h = Math.floor(diff / 3600000)
  const m = Math.floor((diff % 3600000) / 60000)
  if (h > 0) return `${t('days.in')} ${h}h${m > 0 ? String(m).padStart(2, '0') + 'm' : ''}`
  return `${t('days.in')} ${m}m`
}

const scanCountdown     = computed(() => formatCountdown(scanNext.value))
const deletionCountdown = computed(() => formatCountdown(deletionNext.value))

function progressPercent(nextIso, intervalMinutes) {
  if (!nextIso || !intervalMinutes) return 0
  const intervalMs  = Number(intervalMinutes) * 60000
  const remaining   = new Date(nextIso) - now.value
  const elapsed     = intervalMs - remaining
  return Math.min(100, Math.max(0, Math.round((elapsed / intervalMs) * 100)))
}

const scanIntervalMin     = computed(() => Number(settings.settings.scan_interval_minutes || 360))
const deletionIntervalMin = computed(() => Number(settings.settings.deletion_check_interval_minutes || 60))
const scanProgress        = computed(() => progressPercent(scanNext.value, scanIntervalMin.value))
const deletionProgress    = computed(() => progressPercent(deletionNext.value, deletionIntervalMin.value))

onMounted(async () => {
  // Ne rien faire si pas de token — évite les 401 en boucle sur la page login
  if (!localStorage.getItem('hygie_token')) return

  await settings.fetch()

  try {
    const { data } = await api.get('/version')
    version.value = data.version || ''
  } catch { /* silent */ }

  clockInterval = setInterval(() => { now.value = new Date() }, 10000)
})

onUnmounted(() => {
  clearInterval(clockInterval)
})
</script>

<style scoped>
@keyframes scan-pulse {
  0%   { width: 15%; margin-left: 0%;  background: var(--accent); opacity: 1; }
  50%  { width: 40%; margin-left: 30%; background: var(--accent); opacity: 0.7; }
  100% { width: 15%; margin-left: 85%; background: var(--accent); opacity: 1; }
}
@keyframes deletion-pulse {
  0%   { width: 15%; margin-left: 0%;  background: #ef4444; opacity: 1; }
  50%  { width: 40%; margin-left: 30%; background: #ef4444; opacity: 0.7; }
  100% { width: 15%; margin-left: 85%; background: #ef4444; opacity: 1; }
}
.animate-scan-running     { animation: scan-pulse     1.6s ease-in-out infinite; }
.animate-deletion-running { animation: deletion-pulse 1.6s ease-in-out infinite; }

/* Bouncing dots — more visible than opacity-only blink */
@keyframes dot-bounce {
  0%, 60%, 100% { transform: translateY(0);    opacity: 0.35; }
  30%            { transform: translateY(-4px); opacity: 1;    }
}
.dots-anim span {
  display: inline-block;
  font-size: 0.55em;
  animation: dot-bounce 1.1s ease-in-out infinite;
}
.dots-anim span:nth-child(1) { animation-delay: 0s;    }
.dots-anim span:nth-child(2) { animation-delay: 0.18s; }
.dots-anim span:nth-child(3) { animation-delay: 0.36s; }
</style>
