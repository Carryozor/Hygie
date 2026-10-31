<template>
  <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-4">
    <div class="flex items-center gap-3">
      <div class="w-9 h-9 rounded-lg bg-[#2F67BA]/20 flex items-center justify-center">
        <ServiceIcon name="qbittorrent" :size="18" />
      </div>
      <h2 class="font-semibold">qBittorrent</h2>
    </div>

    <div>
      <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.qbit.url') }}</label>
      <div class="flex gap-2">
        <input v-model="form.qbit_url" type="url" :placeholder="t('settings.qbit.urlPlaceholder')" class="flex-1 field font-mono" />
        <TestBtn service="qbit" />
      </div>
    </div>

    <div class="grid grid-cols-2 gap-4">
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">{{ t('common.user') }}</label>
        <input v-model="form.qbit_user" type="text" class="field" />
      </div>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">{{ t('common.password') }}</label>
        <div class="flex gap-2">
          <input v-model="form.qbit_password" :type="showPwd ? 'text' : 'password'" class="flex-1 field" />
          <button type="button" class="px-3 py-2 border border-[var(--border)] rounded-lg text-[var(--muted)] hover:text-white" @click="togglePwd">
            <i :class="['fas', showPwd ? 'fa-eye-slash' : 'fa-eye', 'text-sm']" />
          </button>
        </div>
      </div>
    </div>

    <div class="flex items-center gap-3 py-1">
      <div class="flex-1 h-px bg-[var(--border)]" />
      <div class="flex items-center gap-2 px-3 py-1.5 rounded-full bg-[var(--bg3)] border border-[var(--border)] text-xs text-[var(--muted)]">
        <span>{{ t('settings.qbit.orLabel') }}</span>
        <ServiceIcon name="qui" :size="13" />
        <span class="font-medium text-[var(--text)]">{{ t('settings.qbit.quiProxy') }}</span>
      </div>
      <div class="flex-1 h-px bg-[var(--border)]" />
    </div>

    <div>
      <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.qbit.quiProxyOptional') }}</label>
      <div class="flex gap-2">
        <input v-model="form.qbit_proxy_url" :type="showProxy ? 'text' : 'password'" :placeholder="t('settings.qbit.quiProxyPlaceholder')" class="flex-1 field font-mono" />
        <button type="button" class="px-3 py-2 border border-[var(--border)] rounded-lg text-[var(--muted)] hover:text-white" @click="toggleProxy">
          <i :class="['fas', showProxy ? 'fa-eye-slash' : 'fa-eye', 'text-sm']" />
        </button>
        <TestBtn v-if="form.qbit_proxy_url" service="qui" />
      </div>
    </div>

    <div>
      <label class="block text-xs text-[var(--muted)] mb-2">{{ t('settings.qbit.actions.title') }}</label>
      <div class="space-y-2">
        <div class="flex items-center justify-between py-2 px-3 bg-[var(--bg3)] rounded-lg">
          <span class="text-sm">{{ t('settings.qbit.actions.pause') }}</span>
          <ToggleSlider :model-value="form.qbit_action === 'pause'" @update:model-value="form.qbit_action = $event ? 'pause' : ''" />
        </div>
        <div class="flex items-center justify-between py-2 px-3 bg-[var(--bg3)] rounded-lg">
          <span class="text-sm">{{ t('settings.qbit.actions.deleteNoFiles') }}</span>
          <ToggleSlider :model-value="form.qbit_action === 'delete_torrent'" @update:model-value="form.qbit_action = $event ? 'delete_torrent' : ''" />
        </div>
        <div class="flex items-center justify-between py-2 px-3 bg-[var(--bg3)] rounded-lg">
          <span class="text-sm">{{ t('settings.qbit.actions.deleteWithFiles') }}</span>
          <ToggleSlider :model-value="form.qbit_action === 'delete_files'" @update:model-value="form.qbit_action = $event ? 'delete_files' : ''" />
        </div>
        <div v-if="form.qbit_action === 'delete_files'" data-test="linked-crossseeds" class="py-2 px-3 bg-[var(--bg3)] rounded-lg">
          <div class="flex items-center justify-between">
            <span class="text-sm">{{ t('settings.qbit.actions.linkedCrossseeds') }}</span>
            <ToggleSlider v-model="form.qbit_delete_linked_crossseeds" />
          </div>
          <p class="text-xs text-[var(--muted)] mt-1">{{ t('settings.qbit.actions.linkedCrossseedsHelp') }}</p>
        </div>
      </div>
    </div>

    <div>
      <div class="flex items-center justify-between py-2 px-3 bg-[var(--bg3)] rounded-lg">
        <span class="text-sm">{{ t('settings.qbit.actions.applyTag') }}</span>
        <ToggleSlider :model-value="tagEnabled" @update:model-value="onTagToggle" />
      </div>
      <div v-if="tagEnabled" class="mt-2">
        <input ref="tagInput" v-model="form.qbit_tag" type="text" :placeholder="t('settings.qbit.tagDefault')" class="field" />
      </div>
    </div>

    <div>
      <button type="button" data-test="qbit-health-btn" :disabled="healthLoading"
        class="px-3 py-2 border border-[var(--border)] rounded-lg text-sm text-[var(--muted)] hover:text-white disabled:opacity-50"
        @click="checkHealth">
        <i class="fas fa-stethoscope mr-2" />{{ healthLoading ? t('settings.qbit.health.checking') : t('settings.qbit.health.check') }}
      </button>

      <div v-if="healthError || (health && !health.reachable)" data-test="qbit-health-unreachable"
        class="mt-2 text-sm text-red-400">
        {{ t('settings.qbit.health.unreachable') }}
      </div>

      <div v-else-if="health" data-test="qbit-health" class="mt-2 space-y-1 text-sm">
        <p>{{ t('settings.qbit.health.torrents', { n: health.total }) }}</p>
        <p v-if="health.missing_files" class="text-amber-400">
          {{ t('settings.qbit.health.missing', { n: health.missing_files, size: fmtSize(health.missing_files_bytes) }) }}
        </p>
        <p v-if="health.errored" class="text-red-400">{{ t('settings.qbit.health.errored', { n: health.errored }) }}</p>
        <p v-if="health.connection_status === 'firewalled'" data-test="qbit-health-port-warning" class="text-amber-400">
          {{ t('settings.qbit.health.firewalled') }}
        </p>
        <p v-if="!health.problems.length" class="text-[var(--muted)]">{{ t('settings.qbit.health.none') }}</p>
        <div v-else data-test="qbit-health-problems">
          <p class="text-xs text-[var(--muted)] mt-2 mb-1">{{ t('settings.qbit.health.problemsTitle') }}</p>
          <ul class="max-h-48 overflow-y-auto space-y-1">
            <li v-for="p in health.problems" :key="p.hash" class="flex justify-between gap-3 text-xs bg-[var(--bg3)] rounded px-2 py-1">
              <span class="truncate font-mono" :title="p.name">{{ p.name }}</span>
              <span class="shrink-0 text-[var(--muted)]">{{ p.state }} · {{ fmtSize(p.size) }}</span>
            </li>
          </ul>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import ToggleSlider from '@/components/ui/ToggleSlider.vue'
import ServiceIcon from '@/components/ui/ServiceIcon.vue'
import TestBtn from '@/components/ui/TestBtn.vue'
import { isMasked, revealSetting } from '@/composables/useRevealSetting'
import api from '@/api/client'

const { t } = useI18n()
const props    = defineProps({ form: { type: Object, required: true } })
const showPwd   = ref(false)
const showProxy = ref(false)
const tagInput  = ref(null)
const health        = ref(null)
const healthError   = ref(false)
const healthLoading = ref(false)

async function checkHealth() {
  healthLoading.value = true
  healthError.value = false
  try {
    health.value = (await api.get('/qbit/health')).data
  } catch {
    health.value = null
    healthError.value = true
  } finally {
    healthLoading.value = false
  }
}

function fmtSize(bytes) {
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let v = Number(bytes) || 0
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${i ? v.toFixed(1) : v} ${units[i]}`
}

async function togglePwd() {
  if (!showPwd.value && isMasked(props.form.qbit_password)) {
    props.form.qbit_password = await revealSetting('qbit_password')
  }
  showPwd.value = !showPwd.value
}

async function toggleProxy() {
  if (!showProxy.value && isMasked(props.form.qbit_proxy_url)) {
    props.form.qbit_proxy_url = await revealSetting('qbit_proxy_url')
  }
  showProxy.value = !showProxy.value
}

const tagEnabled = computed(() => !!props.form.qbit_tag)

async function onTagToggle(enabled) {
  if (!enabled) {
    props.form.qbit_tag = ''
  } else {
    props.form.qbit_tag = props.form.qbit_tag || t('settings.qbit.tagDefault')
    await nextTick()
    tagInput.value?.focus()
  }
}
</script>

<style scoped>
.field { @apply w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]; }
</style>
