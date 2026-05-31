import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api/client'

export const useServersStore = defineStore('servers', () => {
  const servers = ref([])
  const libraries = ref([])

  async function fetch() {
    const [serversRes, libRes] = await Promise.all([
      api.get('/settings/media-servers'),
      api.get('/libraries'),
    ])
    servers.value   = serversRes.data || []
    libraries.value = libRes.data || []
  }

  function librariesForServer(serverId) {
    return libraries.value.filter(l => String(l.server_id ?? '0') === String(serverId))
  }

  return { servers, libraries, fetch, librariesForServer }
})
