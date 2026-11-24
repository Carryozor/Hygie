<template>
  <div class="min-h-screen flex items-center justify-center bg-[var(--bg)]">
    <div class="w-full max-w-sm p-8 rounded-2xl bg-[var(--bg2)] border border-[var(--border)] space-y-6">
      <div class="flex flex-col items-center gap-3">
        <HygieLogoSvg :size="48" />
        <h1 class="text-xl font-bold">{{ t('auth.login') }}</h1>
      </div>
      <form v-if="!auth.pendingMfa" class="space-y-4" @submit.prevent="submit">
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">{{ t('auth.username') }}</label>
          <input
v-model="username" type="text" required autocomplete="username"
            class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-[var(--accent)]" />
        </div>
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">{{ t('auth.password') }}</label>
          <input
v-model="password" type="password" required autocomplete="current-password"
            class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-[var(--accent)]" />
        </div>
        <p v-if="error" class="text-red-400 text-xs">{{ error }}</p>
        <button
type="submit" :disabled="loading"
          class="w-full bg-[var(--accent)] hover:opacity-90 disabled:opacity-50 rounded-lg py-2.5 text-sm font-semibold transition-opacity">
          {{ loading ? t('auth.loggingIn') : t('auth.loginBtn') }}
        </button>
      </form>
      <form v-else class="space-y-4" @submit.prevent="submitMfa">
        <p class="text-sm font-medium">{{ t('auth.mfa.title') }}</p>
        <div>
          <label for="mfa-code" class="block text-xs text-[var(--muted)] mb-1">
            {{ useBackup ? t('auth.mfa.backupLabel') : t('auth.mfa.codeLabel') }}
          </label>
          <input
id="mfa-code" v-model="code" data-test="mfa-code" type="text" required autofocus
            :autocomplete="useBackup ? 'off' : 'one-time-code'"
            :inputmode="useBackup ? 'text' : 'numeric'"
            :maxlength="useBackup ? 24 : 8"
            :placeholder="useBackup ? t('auth.mfa.backupPlaceholder') : '123456'"
            class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm font-mono tracking-widest focus:outline-none focus:border-[var(--accent)]" />
          <p class="text-xs text-[var(--muted)] mt-1">{{ useBackup ? t('auth.mfa.backupHint') : t('auth.mfa.codeHint') }}</p>
        </div>
        <p v-if="error" role="alert" class="text-red-400 text-xs">{{ error }}</p>
        <button
type="submit" :disabled="loading"
          class="w-full bg-[var(--accent)] hover:opacity-90 disabled:opacity-50 rounded-lg py-2.5 text-sm font-semibold transition-opacity">
          {{ loading ? t('auth.mfa.verifying') : t('auth.mfa.verify') }}
        </button>
        <div class="flex items-center justify-between text-xs">
          <button type="button" data-test="mfa-back" class="text-[var(--muted)] hover:text-white" @click="back">
            {{ t('auth.mfa.back') }}
          </button>
          <button type="button" data-test="mfa-toggle-backup" class="text-[var(--accent)] hover:underline" @click="toggleBackup">
            {{ useBackup ? t('auth.mfa.useTotp') : t('auth.mfa.useBackup') }}
          </button>
        </div>
      </form>
    </div>
  </div>
</template>
<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '@/stores/auth'
import HygieLogoSvg from '@/components/ui/HygieLogoSvg.vue'

const { t } = useI18n()
const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)
const code = ref('')
const useBackup = ref(false)

function goNext() {
  router.push(route.query.redirect || '/')
}

async function submit() {
  error.value = ''
  loading.value = true
  try {
    const { mfaRequired } = await auth.login(username.value, password.value)
    // Never navigate while a second factor is pending: no access token yet.
    if (mfaRequired) {
      password.value = ''
      code.value = ''
      useBackup.value = false
      return
    }
    goNext()
  } catch {
    error.value = 'Identifiants incorrects'
  } finally {
    loading.value = false
  }
}

async function submitMfa() {
  error.value = ''
  loading.value = true
  try {
    await auth.verifyMfa(code.value)
    goNext()
  } catch (e) {
    // mfa_challenge_invalid already cleared pendingMfa in the store → the
    // template falls back to step 1; the server detail explains why.
    error.value = e?.response?.data?.detail || t('auth.mfa.genericError')
    code.value = ''
  } finally {
    loading.value = false
  }
}

function back() {
  auth.cancelMfa()
  error.value = ''
  code.value = ''
  useBackup.value = false
}

function toggleBackup() {
  useBackup.value = !useBackup.value
  code.value = ''
  error.value = ''
}
</script>
