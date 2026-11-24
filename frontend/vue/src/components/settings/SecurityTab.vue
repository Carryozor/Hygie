<template>
  <div class="space-y-6">
    <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-5">
      <div class="flex items-center gap-3">
        <i class="fas fa-shield-halved text-[var(--accent)]" />
        <h2 class="font-semibold">{{ t('settings.security.title') }}</h2>
      </div>
      <p class="text-sm text-[var(--muted)]">{{ t('settings.security.intro') }}</p>

      <p v-if="error" data-test="error" role="alert" class="text-red-400 text-xs">{{ error }}</p>

      <!-- Backup codes: shown once, right after activation -->
      <div v-if="backupCodes" data-test="codes" class="space-y-4">
        <div class="bg-yellow-500/10 border border-yellow-500/30 text-yellow-400 rounded-lg px-3 py-2 text-sm">
          {{ t('settings.security.backupWarning') }}
        </div>
        <ul class="grid grid-cols-2 gap-2 font-mono text-sm bg-[var(--bg3)] rounded-lg p-4">
          <li v-for="c in backupCodes" :key="c">{{ c }}</li>
        </ul>
        <div class="flex gap-2">
          <button type="button" data-test="codes-copy" class="px-3 py-2 border border-[var(--border)] rounded-lg text-sm hover:bg-[var(--bg3)]" @click="copyCodes">
            <i class="fas fa-copy mr-1" /> {{ copied ? t('settings.security.copied') : t('settings.security.copy') }}
          </button>
          <button type="button" data-test="codes-download" class="px-3 py-2 border border-[var(--border)] rounded-lg text-sm hover:bg-[var(--bg3)]" @click="downloadCodes">
            <i class="fas fa-download mr-1" /> {{ t('settings.security.download') }}
          </button>
        </div>
        <label class="flex items-center gap-2 text-sm cursor-pointer">
          <input v-model="acknowledged" data-test="codes-ack" type="checkbox" />
          {{ t('settings.security.ack') }}
        </label>
        <button
          type="button" data-test="codes-done" :disabled="!acknowledged"
          class="bg-[var(--accent)] hover:opacity-90 disabled:opacity-50 rounded-lg px-4 py-2 text-sm font-semibold"
          @click="finishCodes"
        >{{ t('settings.security.done') }}</button>
      </div>

      <!-- Loading -->
      <p v-else-if="!status" class="text-sm text-[var(--muted)]">{{ t('common.loading') }}</p>

      <!-- Enabled: status + disable -->
      <div v-else-if="status.enabled" class="space-y-4">
        <div class="flex items-center gap-2 text-sm text-green-400">
          <i class="fas fa-circle-check" /> {{ t('settings.security.statusOn') }}
        </div>
        <p class="text-xs text-[var(--muted)]">
          {{ t('settings.security.backupRemaining', { n: status.backup_codes_remaining }) }}
        </p>
        <h3 class="text-sm font-medium pt-2">{{ t('settings.security.disableTitle') }}</h3>
        <p class="text-xs text-[var(--muted)]">{{ t('settings.security.disableHelp') }}</p>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.security.passwordLabel') }}</label>
            <input v-model="disablePassword" data-test="disable-password" type="password" autocomplete="current-password" class="field" />
          </div>
          <div>
            <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.security.disableCode') }}</label>
            <input v-model="disableCode" data-test="disable-code" type="text" autocomplete="off" class="field font-mono" />
          </div>
        </div>
        <button
          type="button" data-test="disable-confirm" :disabled="busy || !disablePassword || !disableCode.trim()"
          class="bg-red-500/80 hover:opacity-90 disabled:opacity-50 rounded-lg px-4 py-2 text-sm font-semibold"
          @click="disable"
        >{{ busy ? t('settings.security.disabling') : t('settings.security.disableBtn') }}</button>
      </div>

      <!-- Setup step 2: QR + code -->
      <div v-else-if="setup" class="space-y-4">
        <p class="text-sm">{{ t('settings.security.scanQr') }}</p>
        <img data-test="qr" :src="setup.qr_svg_data_uri" :alt="t('settings.security.qrAlt')" class="w-48 h-48 bg-white rounded-lg p-2" />
        <div>
          <div class="text-xs text-[var(--muted)] mb-1">{{ t('settings.security.secretLabel') }}</div>
          <code class="block bg-[var(--bg3)] rounded-lg px-3 py-2 text-sm font-mono break-all select-all">{{ setup.secret }}</code>
          <p class="text-xs text-[var(--muted)] mt-1">{{ t('settings.security.secretHelp') }}</p>
        </div>
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.security.codeLabel') }}</label>
          <input v-model="enableCode" data-test="enable-code" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="8" placeholder="123456" class="field font-mono tracking-widest" />
        </div>
        <div class="flex gap-2">
          <button
            type="button" data-test="enable-confirm" :disabled="busy || !enableCode.trim()"
            class="bg-[var(--accent)] hover:opacity-90 disabled:opacity-50 rounded-lg px-4 py-2 text-sm font-semibold"
            @click="enable"
          >{{ busy ? t('settings.security.enabling') : t('settings.security.confirmEnable') }}</button>
          <button type="button" data-test="setup-cancel" class="px-4 py-2 border border-[var(--border)] rounded-lg text-sm" @click="cancelSetup">
            {{ t('common.cancel') }}
          </button>
        </div>
      </div>

      <!-- Setup step 1: password -->
      <div v-else class="space-y-4">
        <div class="flex items-center gap-2 text-sm text-[var(--muted)]">
          <i class="fas fa-circle-xmark" /> {{ t('settings.security.statusOff') }}
        </div>
        <div
          v-if="!status.encryption_available" data-test="encryption-warning"
          class="bg-yellow-500/10 border border-yellow-500/30 text-yellow-400 rounded-lg px-3 py-2 text-sm"
        >{{ t('settings.security.encryptionUnavailable') }}</div>
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.security.passwordLabel') }}</label>
          <input v-model="setupPassword" data-test="setup-password" type="password" autocomplete="current-password" class="field" />
        </div>
        <button
          type="button" data-test="setup-start"
          :disabled="busy || !status.encryption_available || !setupPassword"
          class="bg-[var(--accent)] hover:opacity-90 disabled:opacity-50 rounded-lg px-4 py-2 text-sm font-semibold"
          @click="startSetup"
        >{{ busy ? t('settings.security.starting') : t('settings.security.startSetup') }}</button>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const { t } = useI18n()
const auth = useAuthStore()

const status = ref(null)
const error = ref('')
const busy = ref(false)

const setupPassword = ref('')
const setup = ref(null)
const enableCode = ref('')
const backupCodes = ref(null)
const acknowledged = ref(false)
const copied = ref(false)
const disablePassword = ref('')
const disableCode = ref('')

function describeError(e) {
  return e?.response?.data?.detail || t('settings.security.errorGeneric')
}

async function loadStatus() {
  try {
    const { data } = await api.get('/auth/mfa')
    status.value = data
  } catch (e) {
    error.value = describeError(e)
  }
}

// Run an action with the shared busy/error handling. A 409 "already
// enabled/not enabled" means our view is stale: reload the status.
async function run(action) {
  error.value = ''
  busy.value = true
  try {
    return await action()
  } catch (e) {
    error.value = describeError(e)
    if (e?.response?.status === 409) await loadStatus()
    return undefined
  } finally {
    busy.value = false
  }
}

async function startSetup() {
  if (!status.value?.encryption_available) return
  const data = await run(async () => {
    const res = await api.post('/auth/mfa/setup', { current_password: setupPassword.value })
    return res.data
  })
  if (data) {
    setup.value = data
    setupPassword.value = ''
  }
}

function cancelSetup() {
  setup.value = null
  enableCode.value = ''
  error.value = ''
}

async function enable() {
  const data = await run(async () => {
    const res = await api.post('/auth/mfa/enable', { code: enableCode.value.trim() })
    return res.data
  })
  if (!data) return
  if (data.access_token) auth.adoptAccessToken(data.access_token)
  setup.value = null
  enableCode.value = ''
  acknowledged.value = false
  backupCodes.value = data.backup_codes
}

async function finishCodes() {
  backupCodes.value = null
  acknowledged.value = false
  copied.value = false
  await loadStatus()
}

async function copyCodes() {
  try {
    await navigator.clipboard.writeText(backupCodes.value.join('\n'))
    copied.value = true
  } catch {
    copied.value = false // clipboard unavailable (insecure context): codes stay visible
  }
}

// Built locally from a Blob: the codes never leave the browser.
function downloadCodes() {
  const blob = new Blob([backupCodes.value.join('\n') + '\n'], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'hygie-backup-codes.txt'
  a.click()
  URL.revokeObjectURL(url)
}

async function disable() {
  const data = await run(async () => {
    const res = await api.post('/auth/mfa/disable', {
      current_password: disablePassword.value,
      code: disableCode.value.trim(),
    })
    return res.data
  })
  if (!data) return
  if (data.access_token) auth.adoptAccessToken(data.access_token)
  disablePassword.value = ''
  disableCode.value = ''
  await loadStatus()
}

onMounted(loadStatus)
</script>

<style scoped>
.field { @apply w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]; }
</style>
