<template>
  <div class="min-h-screen flex items-center justify-center bg-[var(--bg)]">
    <div class="w-full max-w-sm p-8 rounded-2xl bg-[var(--bg2)] border border-[var(--border)] space-y-6">
      <div class="flex flex-col items-center gap-3">
        <HygieLogoSvg :size="48" />
        <h1 class="text-xl font-bold">Connexion</h1>
      </div>
      <form @submit.prevent="submit" class="space-y-4">
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Nom d'utilisateur</label>
          <input v-model="username" type="text" required autocomplete="username"
            class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-[var(--accent)]" />
        </div>
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Mot de passe</label>
          <input v-model="password" type="password" required autocomplete="current-password"
            class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-[var(--accent)]" />
        </div>
        <p v-if="error" class="text-red-400 text-xs">{{ error }}</p>
        <button type="submit" :disabled="loading"
          class="w-full bg-[var(--accent)] hover:opacity-90 disabled:opacity-50 rounded-lg py-2.5 text-sm font-semibold transition-opacity">
          {{ loading ? 'Connexion...' : 'Se connecter' }}
        </button>
      </form>
    </div>
  </div>
</template>
<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import HygieLogoSvg from '@/components/ui/HygieLogoSvg.vue'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  loading.value = true
  try {
    await auth.login(username.value, password.value)
    router.push(route.query.redirect || '/')
  } catch {
    error.value = 'Identifiants incorrects'
  } finally {
    loading.value = false
  }
}
</script>
