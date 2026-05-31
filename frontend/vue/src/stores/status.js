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
    // Au moins un serveur fonctionne → logo vert (fonctionnel)
    if (serverStatus.value === 'ok' || serverStatus.value === 'unknown') return 'ok'
    // Tous les serveurs KO → violet (non connecté)
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

  // ── Server health ─────────────────────────────────────────────────────────
  async function checkServerHealth() {
    const servers = useServersStore()
    const srvList = (servers.servers || []).filter(
      s => s.id !== undefined && s.enabled !== false
    )
    if (!srvList.length) {
      serverStatus.value = 'none'
      serverError.value  = false
      return
    }
    let ok = 0, fail = 0
    for (const srv of srvList) {
      try {
        const { data } = await api.post(`/settings/media-servers/${srv.id}/test`)
        data.ok ? ok++ : fail++
      } catch { fail++ }
    }
    serverError.value  = fail > 0
    serverStatus.value = fail === 0 ? 'ok' : ok > 0 ? 'unknown' : 'error'
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
    serverError, serverStatus, hasUnseenErrors, logoStatus,
    fetchScheduler, checkServerHealth, checkUnseenErrors,
    start, stop,
  }
})
