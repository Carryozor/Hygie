// frontend/vue/src/stores/status.js
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api from '@/api/client'
import { useServersStore } from '@/stores/servers'

export const useStatusStore = defineStore('status', () => {
  // ── Scheduler state ──────────────────────────────────────────────────────
  const scanNext        = ref(null)
  const deletionNext    = ref(null)
  const scanRunning     = ref(false)
  const deletionRunning = ref(false)

  // ── Server health state ──────────────────────────────────────────────────
  const serverError  = ref(false)
  const serverStatus = ref('none')  // 'none'|'ok'|'unknown'|'error'

  // ── Log state ────────────────────────────────────────────────────────────
  const hasUnseenErrors = ref(false)

  // ── Computed logo status ─────────────────────────────────────────────────
  const logoStatus = computed(() => {
    if (hasUnseenErrors.value) return 'error'
    if (serverStatus.value === 'ok') return 'ok'
    if (serverStatus.value === 'unknown' || serverStatus.value === 'error') return 'unknown'
    return 'none'
  })

  // ── Internal intervals ───────────────────────────────────────────────────
  let _schedulerInterval = null
  let _healthInterval    = null
  let _logsInterval      = null

  // ── Scheduler polling ─────────────────────────────────────────────────────
  async function fetchScheduler() {
    try {
      const { data } = await api.get('/scheduler/status')
      const scan     = data.find(j => j.id === 'scan_job')
      const deletion = data.find(j => j.id === 'deletion_job')
      scanNext.value        = scan?.next_run      || null
      deletionNext.value    = deletion?.next_run  || null
      scanRunning.value     = scan?.is_running    || false
      deletionRunning.value = deletion?.is_running || false

      const anyRunning = scanRunning.value || deletionRunning.value
      if (_schedulerInterval) {
        clearInterval(_schedulerInterval)
        _schedulerInterval = setInterval(fetchScheduler, anyRunning ? 3000 : 30000)
      }
    } catch { /* silent */ }
  }

  // ── Per-server results (up to 3, mapped to the 3 logo arcs) ─────────────
  const serverResults = ref([])   // [{ok: bool}] — one per enabled server, max 3

  // ── Server health ─────────────────────────────────────────────────────────
  async function checkServerHealth() {
    const servers = useServersStore()
    const srvList = (servers.servers || []).filter(
      s => s.id !== undefined && s.enabled !== false
    )
    if (!srvList.length) {
      serverStatus.value  = 'none'
      serverError.value   = false
      serverResults.value = []
      return
    }
    let ok = 0, fail = 0
    const results = []
    for (const srv of srvList) {
      try {
        const { data } = await api.post(`/settings/media-servers/${srv.id}/test`)
        const isOk = !!data.ok
        isOk ? ok++ : fail++
        results.push({ ok: isOk, type: srv.type || 'emby' })
      } catch {
        fail++
        results.push({ ok: false, type: srv.type || 'emby' })
      }
    }
    serverError.value   = fail > 0
    serverStatus.value  = fail === 0 ? 'ok' : ok > 0 ? 'unknown' : 'error'
    serverResults.value = results.slice(0, 3)
  }

  // ── Unseen error logs ─────────────────────────────────────────────────────
  async function checkUnseenErrors() {
    try {
      const { data } = await api.get('/logs/unseen-errors-count')
      hasUnseenErrors.value = (data.count || 0) > 0
    } catch { /* silent */ }
  }

  // ── Lifecycle ─────────────────────────────────────────────────────────────
  async function start() {
    if (!localStorage.getItem('hygie_token')) return
    const servers = useServersStore()
    await servers.fetch()
    await fetchScheduler()
    checkServerHealth()
    checkUnseenErrors()

    _schedulerInterval = setInterval(fetchScheduler, 30000)
    _healthInterval    = setInterval(checkServerHealth, 120000)
    _logsInterval      = setInterval(checkUnseenErrors, 20000)
  }

  function stop() {
    clearInterval(_schedulerInterval)
    clearInterval(_healthInterval)
    clearInterval(_logsInterval)
    _schedulerInterval = null
    _healthInterval    = null
    _logsInterval      = null
  }

  return {
    scanNext, deletionNext, scanRunning, deletionRunning,
    serverError, serverStatus, serverResults, hasUnseenErrors, logoStatus,
    fetchScheduler, checkServerHealth, checkUnseenErrors,
    start, stop,
  }
})
