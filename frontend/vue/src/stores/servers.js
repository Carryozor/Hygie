import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api/client'

export const useServersStore = defineStore('servers', () => {
  const servers = ref([])
  const libraries = ref([])

  const error = ref(null)

  async function fetch() {
    error.value = null
    try {
      const [serversRes, libRes] = await Promise.all([
        api.get('/settings/media-servers'),
        api.get('/libraries'),
      ])
      servers.value   = serversRes.data || []
      libraries.value = libRes.data || []
    } catch (e) {
      error.value = e?.message || 'fetch error'
    }
  }

  function librariesForServer(serverId) {
    return libraries.value.filter(l => String(l.server_id ?? '0') === String(serverId))
  }

  return { servers, libraries, error, fetch, librariesForServer }
})
