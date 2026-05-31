<template>
  <div class="space-y-6">
    <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-5">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-lg bg-[#5865F2]/20 flex items-center justify-center">
            <ServiceIcon name="discord" :size="22" />
          </div>
          <h2 class="font-semibold">{{ t('settings.discord.title') }}</h2>
        </div>
        <TestBtn service="discord" />
      </div>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.discord.webhookMain') }}</label>
        <div class="flex gap-2">
          <input v-model="form.discord_webhook" :type="showWebhook ? 'text' : 'password'" :placeholder="t('settings.discord.webhookPlaceholder')" class="flex-1 field font-mono" />
          <button type="button" class="px-3 py-2 border border-[var(--border)] rounded-lg text-[var(--muted)] hover:text-white" @click="showWebhook = !showWebhook">
            <i :class="['fas', showWebhook ? 'fa-eye-slash' : 'fa-eye', 'text-sm']" />
          </button>
        </div>
      </div>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.discord.thresholdsLabel') }}</label>
        <input v-model="form.discord_notif_thresholds" type="text" :placeholder="t('settings.discord.thresholdsPlaceholder')" class="field" />
        <p class="text-xs text-[var(--muted)] mt-1">{{ t('settings.discord.thresholdsHelp') }}</p>
      </div>
    </section>

    <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-5">
      <div class="flex items-center justify-between">
        <h2 class="font-semibold text-sm">{{ t('settings.discord.alerts.title') }}</h2>
        <TestBtn service="discord_alerts" />
      </div>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.discord.alerts.webhook') }}</label>
        <div class="flex gap-2">
          <input v-model="form.discord_webhook_alerts" :type="showAlerts ? 'text' : 'password'" :placeholder="t('settings.discord.webhookPlaceholder')" class="flex-1 field font-mono" />
          <button type="button" class="px-3 py-2 border border-[var(--border)] rounded-lg text-[var(--muted)] hover:text-white" @click="showAlerts = !showAlerts">
            <i :class="['fas', showAlerts ? 'fa-eye-slash' : 'fa-eye', 'text-sm']" />
          </button>
        </div>
      </div>
      <div class="space-y-3">
        <AlertRow
          v-model:enabled="form.discord_alert_deletion_error"
          v-model:mention="form.discord_alert_deletion_error_mention"
          v-model:msg="form.discord_alert_deletion_error_msg"
          :label="t('settings.discord.alerts.deletionError')"
        />
        <AlertRow
          v-model:enabled="form.discord_alert_scan_failure"
          v-model:mention="form.discord_alert_scan_failure_mention"
          v-model:msg="form.discord_alert_scan_failure_msg"
          :label="t('settings.discord.alerts.scanFailure')"
        />
        <AlertRow
          v-model:enabled="form.discord_alert_seerr_failure"
          v-model:mention="form.discord_alert_seerr_failure_mention"
          v-model:msg="form.discord_alert_seerr_failure_msg"
          :label="t('settings.discord.alerts.seerrFailure')"
        />
      </div>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.discord.errorThreshold') }}</label>
        <input v-model.number="form.discord_alert_error_threshold" type="number" min="0" class="field" />
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, defineComponent, h } from 'vue'
import { useI18n } from 'vue-i18n'
import ServiceIcon from '@/components/ui/ServiceIcon.vue'
import TestBtn from '@/components/ui/TestBtn.vue'
import ToggleSlider from '@/components/ui/ToggleSlider.vue'

const { t } = useI18n()
defineProps({ form: { type: Object, required: true } })

const showWebhook = ref(false)
const showAlerts  = ref(false)

const AlertRow = defineComponent({
  props: { label: String, enabled: String, mention: String, msg: String },
  emits: ['update:enabled', 'update:mention', 'update:msg'],
  setup(props, { emit }) {
    const isEnabled = computed({
      get: () => props.enabled === 'true' || props.enabled === true,
      set: v => emit('update:enabled', String(v)),
    })
    return () => h('div', { class: 'space-y-2' }, [
      h('div', { class: 'flex items-center justify-between py-2 px-3 bg-[var(--bg3)] rounded-lg' }, [
        h('span', { class: 'text-sm' }, props.label),
        h(ToggleSlider, {
          modelValue: isEnabled.value,
          'onUpdate:modelValue': v => { isEnabled.value = v },
        }),
      ]),
      isEnabled.value && h('div', { class: 'grid grid-cols-2 gap-2 px-1' }, [
        h('input', { type: 'text', placeholder: t('settings.discord.mentionPlaceholder'), value: props.mention || '', onInput: e => emit('update:mention', e.target.value), class: 'w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-[var(--accent)]' }),
        h('input', { type: 'text', placeholder: t('settings.discord.messagePlaceholder'), value: props.msg || '', onInput: e => emit('update:msg', e.target.value), class: 'w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-[var(--accent)]' }),
      ]),
    ])
  },
})
</script>

<style scoped>
.field { @apply w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]; }
</style>
