import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api/client'

export const useStatsStore = defineStore('stats', () => {
  const global  = ref({ total_deleted: 0, total_ignored: 0, total_scans: 0, queue: {}, by_month: [] })
  const storage = ref({ disks: [], movies: {}, series: {}, total_media_size: 0, queue: {} })
  const error   = ref(null)

  async function fetchGlobal() {
    error.value = null
    try {
      const { data } = await api.get('/stats/global')
      global.value = data
    } catch (e) {
      error.value = e?.message || 'fetch error'
    }
  }

  async function fetchStorage() {
    error.value = null
    try {
      const { data } = await api.get('/storage')
      storage.value = data
    } catch (e) {
      error.value = e?.message || 'fetch error'
    }
  }

  return { global, storage, error, fetchGlobal, fetchStorage }
})
