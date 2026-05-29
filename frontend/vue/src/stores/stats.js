import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api/client'

export const useStatsStore = defineStore('stats', () => {
  const global = ref({ total_deleted: 0, total_ignored: 0, total_scans: 0, queue: {}, by_month: [] })
  const storage = ref({ disks: [], movies: {}, series: {}, total_media_size: 0, queue: {} })

  async function fetchGlobal() {
    const { data } = await api.get('/stats/global')
    global.value = data
  }

  async function fetchStorage() {
    const { data } = await api.get('/storage')
    storage.value = data
  }

  return { global, storage, fetchGlobal, fetchStorage }
})
