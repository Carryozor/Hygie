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
        <input v-model="form.qbit_proxy_url" type="url" :placeholder="t('settings.qbit.quiProxyPlaceholder')" class="flex-1 field font-mono" />
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
  </section>
</template>

<script setup>
import { ref, computed, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import ToggleSlider from '@/components/ui/ToggleSlider.vue'
import ServiceIcon from '@/components/ui/ServiceIcon.vue'
import TestBtn from '@/components/ui/TestBtn.vue'
import { isMasked, revealSetting } from '@/composables/useRevealSetting'

const { t } = useI18n()
const props   = defineProps({ form: { type: Object, required: true } })
const showPwd  = ref(false)
const tagInput = ref(null)

async function togglePwd() {
  if (!showPwd.value && isMasked(props.form.qbit_password)) {
    props.form.qbit_password = await revealSetting('qbit_password')
  }
  showPwd.value = !showPwd.value
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
