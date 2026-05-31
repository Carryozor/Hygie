<template>
  <div class="space-y-6">
    <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-5">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-lg bg-[#5865F2]/20 flex items-center justify-center">
            <ServiceIcon name="discord" :size="22" />
          </div>
          <h2 class="font-semibold">Notifications Discord</h2>
        </div>
        <TestBtn service="discord" />
      </div>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Webhook principal (notifications suppressions)</label>
        <div class="flex gap-2">
          <input v-model="form.discord_webhook" :type="showWebhook ? 'text' : 'password'" placeholder="https://discord.com/api/webhooks/…" class="flex-1 field font-mono" />
          <button type="button" class="px-3 py-2 border border-[var(--border)] rounded-lg text-[var(--muted)] hover:text-white" @click="showWebhook = !showWebhook">
            <i :class="['fas', showWebhook ? 'fa-eye-slash' : 'fa-eye', 'text-sm']" />
          </button>
        </div>
      </div>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Seuils de notification (jours avant suppression, séparés par virgule)</label>
        <input v-model="form.discord_notif_thresholds" type="text" placeholder="30,14,7,3,1" class="field" />
        <p class="text-xs text-[var(--muted)] mt-1">Un message Discord sera envoyé J-30, J-14, J-7, J-3 et J-1 avant la date de suppression.</p>
      </div>
    </section>

    <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-5">
      <div class="flex items-center justify-between">
        <h2 class="font-semibold text-sm">Alertes système</h2>
        <TestBtn service="discord_alerts" />
      </div>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Webhook alertes (erreurs critiques)</label>
        <div class="flex gap-2">
          <input v-model="form.discord_webhook_alerts" :type="showAlerts ? 'text' : 'password'" placeholder="https://discord.com/api/webhooks/…" class="flex-1 field font-mono" />
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
          label="Erreur de suppression"
        />
        <AlertRow
          v-model:enabled="form.discord_alert_scan_failure"
          v-model:mention="form.discord_alert_scan_failure_mention"
          v-model:msg="form.discord_alert_scan_failure_msg"
          label="Échec de scan"
        />
        <AlertRow
          v-model:enabled="form.discord_alert_seerr_failure"
          v-model:mention="form.discord_alert_seerr_failure_mention"
          v-model:msg="form.discord_alert_seerr_failure_msg"
          label="Échec Seerr"
        />
      </div>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Seuil d'erreurs avant alerte (0 = désactivé)</label>
        <input v-model.number="form.discord_alert_error_threshold" type="number" min="0" class="field" />
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, defineComponent, h } from 'vue'
import ServiceIcon from '@/components/ui/ServiceIcon.vue'
import TestBtn from '@/components/ui/TestBtn.vue'
import ToggleSlider from '@/components/ui/ToggleSlider.vue'

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
        h('input', { type: 'text', placeholder: 'Mention (@role / @user)', value: props.mention || '', onInput: e => emit('update:mention', e.target.value), class: 'w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-[var(--accent)]' }),
        h('input', { type: 'text', placeholder: 'Message personnalisé', value: props.msg || '', onInput: e => emit('update:msg', e.target.value), class: 'w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-[var(--accent)]' }),
      ]),
    ])
  },
})
</script>

<style scoped>
.field { @apply w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]; }
</style>
