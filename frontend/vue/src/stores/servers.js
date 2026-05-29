import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api/client'

export const useServersStore = defineStore('servers', () => {
  const servers = ref([])
  const libraries = ref([])

  async function fetch() {
    const [settingsRes, libRes] = await Promise.all([
      api.get('/settings'),
      api.get('/libraries'),
    ])
    const raw = settingsRes.data?.media_servers
    servers.value = typeof raw === 'string' ? JSON.parse(raw) : (raw || [])
    libraries.value = libRes.data || []
  }

  function librariesForServer(serverId) {
    return libraries.value.filter(l => String(l.server_id) === String(serverId))
  }

  return { servers, libraries, fetch, librariesForServer }
})
