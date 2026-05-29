<!-- frontend/vue/src/views/SettingsView.vue -->
<template>
  <div class="max-w-2xl space-y-8">
    <div v-if="saved" role="status" class="bg-green-500/20 border border-green-500/30 text-green-400 rounded-lg px-4 py-3 text-sm">
      Paramètres sauvegardés.
    </div>

    <!-- Général -->
    <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-5">
      <h2 class="font-semibold">Général</h2>

      <div class="flex items-center justify-between">
        <div>
          <div class="text-sm font-medium">Mode Dry Run</div>
          <div class="text-xs text-[var(--muted)]">Simule les suppressions sans les exécuter</div>
        </div>
        <ToggleSlider v-model="form.dry_run" />
      </div>

      <div class="flex items-center justify-between">
        <div>
          <div class="text-sm font-medium">Sauvegarde automatique</div>
          <div class="text-xs text-[var(--muted)]">Sauvegarde la base de données avant suppression</div>
        </div>
        <ToggleSlider v-model="form.backup_enabled" />
      </div>
    </section>

    <!-- Intervalles -->
    <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-4">
      <h2 class="font-semibold">Intervalles</h2>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Scan (minutes)</label>
          <input v-model.number="form.scan_interval_minutes" type="number" min="10"
            class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]" />
        </div>
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Suppression (minutes)</label>
          <input v-model.number="form.deletion_check_interval_minutes" type="number" min="10"
            class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]" />
        </div>
      </div>
    </section>

    <!-- Plex -->
    <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-5">
      <div class="flex items-center gap-2">
        <i class="fas fa-play-circle text-[var(--accent)]" />
        <h2 class="font-semibold">Plex</h2>
      </div>

      <!-- Plex.tv token -->
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Token Plex.tv</label>
        <div class="flex gap-2">
          <input
            v-model="form.plex_tv_token"
            :type="showPlexToken ? 'text' : 'password'"
            placeholder="Votre token Plex.tv…"
            class="flex-1 bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:border-[var(--accent)]"
          />
          <button type="button" class="px-3 py-2 border border-[var(--border)] rounded-lg text-[var(--muted)] hover:text-[var(--text)] transition-colors" @click="showPlexToken = !showPlexToken">
            <i :class="['fas', showPlexToken ? 'fa-eye-slash' : 'fa-eye', 'text-sm']" />
          </button>
        </div>
        <p class="text-xs text-[var(--muted)] mt-1">Utilisé pour découvrir vos serveurs Plex et vos amis.</p>
      </div>

      <!-- Webhook secret -->
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Secret Webhook Plex</label>
        <div class="flex gap-2">
          <input
            v-model="form.plex_webhook_secret"
            :type="showWebhookSecret ? 'text' : 'password'"
            placeholder="Secret optionnel…"
            class="flex-1 bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:border-[var(--accent)]"
          />
          <button type="button" class="px-3 py-2 border border-[var(--border)] rounded-lg text-[var(--muted)] hover:text-[var(--text)] transition-colors" @click="showWebhookSecret = !showWebhookSecret">
            <i :class="['fas', showWebhookSecret ? 'fa-eye-slash' : 'fa-eye', 'text-sm']" />
          </button>
        </div>
        <p class="text-xs text-[var(--muted)] mt-1">
          Configurez l'URL webhook Plex :
          <code class="bg-[var(--bg3)] px-1 rounded">{{ webhookUrl }}</code>
        </p>
      </div>

      <!-- Overlay affiches Plex -->
      <div class="flex items-center justify-between">
        <div>
          <div class="text-sm font-medium">Overlay « Supprimé dans Xj » sur Plex</div>
          <div class="text-xs text-[var(--muted)]">Applique la bannière sur les affiches des médias en file de suppression</div>
        </div>
        <ToggleSlider v-model="form.plex_overlay_enabled" />
      </div>
    </section>

    <button
      @click="save"
      :disabled="saving"
      class="bg-[var(--accent)] hover:opacity-90 disabled:opacity-50 rounded-lg px-6 py-2.5 text-sm font-semibold transition-opacity"
    >
      {{ saving ? 'Enregistrement...' : 'Enregistrer' }}
    </button>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useSettingsStore } from '@/stores/settings'
import ToggleSlider from '@/components/ui/ToggleSlider.vue'

const settings = useSettingsStore()
const form   = ref({})
const saving = ref(false)
const saved  = ref(false)
const showPlexToken    = ref(false)
const showWebhookSecret = ref(false)

const webhookUrl = computed(() => {
  const base = window.location.origin
  const secret = form.value.plex_webhook_secret
  return secret ? `${base}/api/plex/webhook?secret=${secret}` : `${base}/api/plex/webhook`
})

function syncForm() {
  form.value = {
    dry_run:                         settings.settings.dry_run === 'true' || settings.settings.dry_run === true,
    backup_enabled:                  settings.settings.backup_enabled === 'true' || settings.settings.backup_enabled === true,
    scan_interval_minutes:           Number(settings.settings.scan_interval_minutes || 360),
    deletion_check_interval_minutes: Number(settings.settings.deletion_check_interval_minutes || 60),
    plex_tv_token:                   settings.settings.plex_tv_token || '',
    plex_webhook_secret:             settings.settings.plex_webhook_secret || '',
    plex_overlay_enabled:            settings.settings.plex_overlay_enabled === 'true',
  }
}

watch(() => settings.settings, syncForm, { deep: true })

async function save() {
  saving.value = true
  saved.value  = false
  try {
    await settings.save({
      dry_run:                         String(form.value.dry_run),
      backup_enabled:                  String(form.value.backup_enabled),
      scan_interval_minutes:           String(form.value.scan_interval_minutes),
      deletion_check_interval_minutes: String(form.value.deletion_check_interval_minutes),
      plex_tv_token:                   form.value.plex_tv_token,
      plex_webhook_secret:             form.value.plex_webhook_secret,
      plex_overlay_enabled:            String(form.value.plex_overlay_enabled),
    })
    saved.value = true
    setTimeout(() => { saved.value = false }, 3000)
  } finally {
    saving.value = false
  }
}

onMounted(async () => { await settings.fetch(); syncForm() })
</script>
