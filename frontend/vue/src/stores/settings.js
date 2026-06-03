import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api/client'

export const useSettingsStore = defineStore('settings', () => {
  const settings = ref({})
  const loading = ref(false)

  async function fetch() {
    loading.value = true
    try {
      const { data } = await api.get('/settings')
      settings.value = data
    } finally {
      loading.value = false
    }
  }

  async function save(patch) {
    await api.post('/settings', patch)
    Object.assign(settings.value, patch)
  }

  return { settings, loading, fetch, save }
})
