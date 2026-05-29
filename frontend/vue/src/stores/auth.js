import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api from '@/api/client'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('hygie_token') || '')
  const username = ref('')
  const setupComplete = ref(null)

  const isLoggedIn = computed(() => !!token.value)

  async function checkSetup() {
    const { data } = await api.get('/auth/status')
    setupComplete.value = data.setup_complete
    return data.setup_complete
  }

  async function setup(u, p) {
    const { data } = await api.post('/auth/setup', { username: u, password: p })
    token.value = data.token
    username.value = data.username
    localStorage.setItem('hygie_token', data.token)
    setupComplete.value = true
  }

  async function login(u, p) {
    const { data } = await api.post('/auth/login', { username: u, password: p })
    token.value = data.token
    username.value = data.username || u
    localStorage.setItem('hygie_token', data.token)
  }

  async function fetchMe() {
    if (!token.value) return
    const { data } = await api.get('/auth/me')
    username.value = data.username
  }

  function logout() {
    token.value = ''
    username.value = ''
    localStorage.removeItem('hygie_token')
  }

  return { token, username, setupComplete, isLoggedIn, checkSetup, setup, login, fetchMe, logout }
})
