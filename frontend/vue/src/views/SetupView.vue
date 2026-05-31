<template>
  <div class="min-h-screen flex items-center justify-center bg-[var(--bg)]">
    <div class="w-full max-w-sm p-8 rounded-2xl bg-[var(--bg2)] border border-[var(--border)] space-y-6">
      <div class="flex flex-col items-center gap-3">
        <HygieLogoSvg :size="48" />
        <h1 class="text-2xl font-bold">Bienvenue sur Hygie</h1>
        <p class="text-[var(--muted)] text-sm text-center">Créez votre compte administrateur pour commencer.</p>
      </div>
      <form class="space-y-4" @submit.prevent="submit">
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Nom d'utilisateur</label>
          <input
v-model="username" type="text" required autocomplete="username"
            class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-[var(--accent)]" />
        </div>
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Mot de passe</label>
          <input
v-model="password" type="password" required autocomplete="new-password"
            class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-[var(--accent)]" />
        </div>
        <p v-if="error" class="text-red-400 text-xs">{{ error }}</p>
        <button
type="submit" :disabled="loading"
          class="w-full bg-[var(--accent)] hover:opacity-90 disabled:opacity-50 rounded-lg py-2.5 text-sm font-semibold transition-opacity">
          {{ loading ? 'Création...' : 'Créer le compte' }}
        </button>
      </form>
    </div>
  </div>
</template>
<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import HygieLogoSvg from '@/components/ui/HygieLogoSvg.vue'

const auth = useAuthStore()
const router = useRouter()
const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  loading.value = true
  try {
    await auth.setup(username.value, password.value)
    router.push('/')
  } catch (e) {
    error.value = e.response?.data?.detail || 'Erreur lors de la création du compte'
  } finally {
    loading.value = false
  }
}
</script>
