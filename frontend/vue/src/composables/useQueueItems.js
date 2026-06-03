// frontend/vue/src/composables/useQueueItems.js
import { ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import api from '@/api/client'

export function useQueueItems() {
  const { t, locale } = useI18n()

  const items        = ref([])
  const total        = ref(0)
  const page         = ref(1)
  const statusFilter = ref('')
  const search       = ref('')
  const sort         = ref('delete_at')
  const dir          = ref('asc')
  const loading      = ref(false)
  const error        = ref('')
  const perPage      = 50

  const totalPages = computed(() => Math.ceil(total.value / perPage))

  const visiblePages = computed(() => {
    const n = totalPages.value, p = page.value
    if (n <= 7) return Array.from({ length: n }, (_, i) => i + 1)
    const start = Math.max(1, p - 2), end = Math.min(n, p + 2)
    const pages = []
    if (start > 2)        pages.push(1, '…')
    else if (start === 2) pages.push(1)
    for (let i = start; i <= end; i++) pages.push(i)
    if (end < n - 1)      pages.push('…', n)
    else if (end === n - 1) pages.push(n)
    return pages
  })

  async function load() {
    loading.value = true; error.value = ''
    try {
      const params = { limit: perPage, offset: (page.value - 1) * perPage, sort: sort.value, dir: dir.value }
      if (statusFilter.value) params.status = statusFilter.value
      if (search.value) params.search = search.value
      const { data } = await api.get('/media', { params })
      items.value = data.items || data || []
      total.value = data.total  || items.value.length
    } catch {
      error.value = t('queue.error.loadFailed')
    } finally {
      loading.value = false
    }
  }

  function setSort(field) {
    if (sort.value === field) dir.value = dir.value === 'asc' ? 'desc' : 'asc'
    else { sort.value = field; dir.value = 'asc' }
    page.value = 1
    load()
  }

  function setFilter(v) { statusFilter.value = v; page.value = 1 }

  // ── Formatting helpers ──────────────────────────────────────────────────────

  function daysRemaining(deleteAt) {
    if (!deleteAt) return null
    return Math.ceil((new Date(deleteAt) - new Date()) / (1000 * 60 * 60 * 24))
  }

  function daysLabel(deleteAt, status) {
    if (status === 'deleted') return t('status.deleted')
    if (!deleteAt) return '—'
    const d = daysRemaining(deleteAt)
    if (d === null) return '—'
    if (d < 0)  return t('days.exceeded')
    if (d === 0) return t('days.today')
    if (d === 1) return t('days.tomorrow')
    return t('days.inDays', { n: d })
  }

  function daysClass(deleteAt, status) {
    if (status === 'deleted') return 'text-[var(--muted)]'
    const d = daysRemaining(deleteAt)
    if (d === null) return 'text-[var(--muted)]'
    if (d <= 3)  return 'text-red-400'
    if (d <= 7)  return 'text-orange-400'
    if (d <= 14) return 'text-yellow-400'
    return 'text-[var(--muted)]'
  }

  function gridBannerClass(deleteAt, status) {
    if (status === 'deleted') return 'bg-green-600/90'
    if (status === 'error')   return 'bg-red-700/90'
    const d = daysRemaining(deleteAt)
    if (d === null) return 'bg-[var(--bg3)]/80'
    if (d <= 3)  return 'bg-red-600/90'
    if (d <= 7)  return 'bg-orange-600/90'
    if (d <= 14) return 'bg-yellow-600/90'
    return 'bg-black/50'
  }

  function rowUrgencyClass(deleteAt, status) {
    if (status !== 'pending') return ''
    const d = daysRemaining(deleteAt)
    if (d === null) return ''
    if (d <= 3) return 'bg-red-500/5'
    if (d <= 7) return 'bg-orange-500/5'
    return ''
  }

  function formatDate(iso) {
    if (!iso) return ''
    return new Date(iso).toLocaleDateString(locale.value, { day: '2-digit', month: '2-digit', year: 'numeric' })
  }

  const STATUS_CLASSES = {
    pending: 'bg-yellow-500/20 text-yellow-400',
    deleted: 'bg-green-500/20 text-green-400',
    error:   'bg-red-700/20 text-red-300',
  }
  function statusLabel(s) {
    const map = { pending: t('status.pending'), deleted: t('status.deleted'), error: t('status.error') }
    return map[s] || s
  }
  function statusClass(s) { return STATUS_CLASSES[s] || 'bg-[var(--bg3)] text-[var(--muted)]' }
  function isSeries(tp) { return tp === 'Episode' || tp === 'Series' || tp === 'Season' }

  return {
    items, total, page, statusFilter, search, sort, dir,
    loading, error, perPage, totalPages, visiblePages,
    load, setSort, setFilter,
    daysRemaining, daysLabel, daysClass, gridBannerClass, rowUrgencyClass,
    formatDate, statusLabel, statusClass, isSeries,
  }
}
