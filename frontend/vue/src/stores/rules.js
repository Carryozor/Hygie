// frontend/vue/src/stores/rules.js
import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api/client'

export const useRulesStore = defineStore('rules', () => {
  const simpleRules  = ref([])
  const expertRules  = ref([])
  const loading      = ref(false)

  async function fetchAll() {
    loading.value = true
    try {
      const [sRes, eRes] = await Promise.all([
        api.get('/seerr-rules').catch(() => ({ data: [] })),
        api.get('/expert-rules'),
      ])
      simpleRules.value = sRes.data || []
      expertRules.value = eRes.data || []
    } finally {
      loading.value = false
    }
  }

  async function createSimpleRule(payload) {
    const { data } = await api.post('/seerr-rules', payload)
    simpleRules.value.push(data)
    return data
  }

  async function updateSimpleRule(id, payload) {
    const { data } = await api.put(`/seerr-rules/${id}`, payload)
    const idx = simpleRules.value.findIndex(r => r.id === id)
    if (idx >= 0) simpleRules.value[idx] = data
    return data
  }

  async function deleteSimpleRule(id) {
    await api.delete(`/seerr-rules/${id}`)
    simpleRules.value = simpleRules.value.filter(r => r.id !== id)
  }

  async function createExpertRule(payload) {
    const { data } = await api.post('/expert-rules', payload)
    expertRules.value.push(data)
    return data
  }

  async function updateExpertRule(id, payload) {
    const { data } = await api.put(`/expert-rules/${id}`, payload)
    const idx = expertRules.value.findIndex(r => r.id === id)
    if (idx >= 0) expertRules.value[idx] = data
    return data
  }

  async function deleteExpertRule(id) {
    await api.delete(`/expert-rules/${id}`)
    expertRules.value = expertRules.value.filter(r => r.id !== id)
  }

  async function toggleExpertRule(id) {
    const rule = expertRules.value.find(r => r.id === id)
    if (!rule) return
    await updateExpertRule(id, { ...rule, enabled: !rule.enabled })
  }

  async function migrateFromLibraries() {
    const { data } = await api.post('/expert-rules/migrate-from-libraries')
    const n = data.created ?? 0
    if (n > 0) await fetchAll()
    return n
  }

  async function runScan(libraryId, libraryIds = null) {
    if (libraryIds?.length) {
      // Multi-library expert rule — scan each targeted library
      for (const libId of libraryIds) {
        await api.post(`/libraries/${libId}/scan`)
      }
    } else if (libraryId) {
      await api.post(`/libraries/${libraryId}/scan`)
    } else {
      await api.post('/scan/trigger')
    }
  }

  return {
    simpleRules, expertRules, loading,
    fetchAll,
    createSimpleRule, updateSimpleRule, deleteSimpleRule,
    createExpertRule, updateExpertRule, deleteExpertRule, toggleExpertRule,
    migrateFromLibraries, runScan,
  }
})
