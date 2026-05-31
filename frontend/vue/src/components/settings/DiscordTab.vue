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

      <!-- Alert rows — inline template instead of defineComponent/h() -->
      <div class="space-y-3">

        <!-- Erreur de suppression -->
        <div class="space-y-2">
          <div class="flex items-center justify-between py-2 px-3 bg-[var(--bg3)] rounded-lg">
            <span class="text-sm">{{ t('settings.discord.alerts.deletionError') }}</span>
            <ToggleSlider v-model="deletionErrorEnabled" />
          </div>
          <div v-if="deletionErrorEnabled" class="grid grid-cols-2 gap-2 px-1">
            <input v-model="form.discord_alert_deletion_error_mention" type="text" :placeholder="t('settings.discord.mentionPlaceholder')" class="field text-xs" />
            <input v-model="form.discord_alert_deletion_error_msg" type="text" :placeholder="t('settings.discord.messagePlaceholder')" class="field text-xs" />
          </div>
        </div>

        <!-- Échec de scan -->
        <div class="space-y-2">
          <div class="flex items-center justify-between py-2 px-3 bg-[var(--bg3)] rounded-lg">
            <span class="text-sm">{{ t('settings.discord.alerts.scanFailure') }}</span>
            <ToggleSlider v-model="scanFailureEnabled" />
          </div>
          <div v-if="scanFailureEnabled" class="grid grid-cols-2 gap-2 px-1">
            <input v-model="form.discord_alert_scan_failure_mention" type="text" :placeholder="t('settings.discord.mentionPlaceholder')" class="field text-xs" />
            <input v-model="form.discord_alert_scan_failure_msg" type="text" :placeholder="t('settings.discord.messagePlaceholder')" class="field text-xs" />
          </div>
        </div>

        <!-- Échec Seerr -->
        <div class="space-y-2">
          <div class="flex items-center justify-between py-2 px-3 bg-[var(--bg3)] rounded-lg">
            <span class="text-sm">{{ t('settings.discord.alerts.seerrFailure') }}</span>
            <ToggleSlider v-model="seerrFailureEnabled" />
          </div>
          <div v-if="seerrFailureEnabled" class="grid grid-cols-2 gap-2 px-1">
            <input v-model="form.discord_alert_seerr_failure_mention" type="text" :placeholder="t('settings.discord.mentionPlaceholder')" class="field text-xs" />
            <input v-model="form.discord_alert_seerr_failure_msg" type="text" :placeholder="t('settings.discord.messagePlaceholder')" class="field text-xs" />
          </div>
        </div>

      </div>

      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">{{ t('settings.discord.errorThreshold') }}</label>
        <input v-model.number="form.discord_alert_error_threshold" type="number" min="0" class="field" />
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import ServiceIcon from '@/components/ui/ServiceIcon.vue'
import TestBtn from '@/components/ui/TestBtn.vue'
import ToggleSlider from '@/components/ui/ToggleSlider.vue'

const { t } = useI18n()
const props = defineProps({ form: { type: Object, required: true } })

const showWebhook = ref(false)
const showAlerts  = ref(false)

// Boolean computed wrappers for ToggleSlider (form stores 'true'/'false' strings)
const deletionErrorEnabled = computed({
  get: () => props.form.discord_alert_deletion_error === 'true' || props.form.discord_alert_deletion_error === true,
  set: v => { props.form.discord_alert_deletion_error = String(v) },
})
const scanFailureEnabled = computed({
  get: () => props.form.discord_alert_scan_failure === 'true' || props.form.discord_alert_scan_failure === true,
  set: v => { props.form.discord_alert_scan_failure = String(v) },
})
const seerrFailureEnabled = computed({
  get: () => props.form.discord_alert_seerr_failure === 'true' || props.form.discord_alert_seerr_failure === true,
  set: v => { props.form.discord_alert_seerr_failure = String(v) },
})
</script>

<style scoped>
.field { @apply w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]; }
</style>
