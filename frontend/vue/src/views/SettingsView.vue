<!-- frontend/vue/src/views/SettingsView.vue -->
<template>
  <div class="max-w-2xl space-y-8">
    <div v-if="saved" class="bg-green-500/20 border border-green-500/30 text-green-400 rounded-lg px-4 py-3 text-sm">
      Paramètres sauvegardés.
    </div>

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

    <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-4">
      <h2 class="font-semibold">Intervalles</h2>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Scan (minutes)</label>
          <input v-model.number="form.scan_interval_minutes" type="number" min="10"
            class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Suppression (minutes)</label>
          <input v-model.number="form.deletion_check_interval_minutes" type="number" min="10"
            class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm" />
        </div>
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
import { ref, onMounted, watch } from 'vue'
import { useSettingsStore } from '@/stores/settings'
import ToggleSlider from '@/components/ui/ToggleSlider.vue'

const settings = useSettingsStore()
const form   = ref({})
const saving = ref(false)
const saved  = ref(false)

function syncForm() {
  form.value = {
    dry_run:                         settings.settings.dry_run === 'true' || settings.settings.dry_run === true,
    backup_enabled:                  settings.settings.backup_enabled === 'true' || settings.settings.backup_enabled === true,
    scan_interval_minutes:           Number(settings.settings.scan_interval_minutes || 360),
    deletion_check_interval_minutes: Number(settings.settings.deletion_check_interval_minutes || 60),
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
    })
    saved.value = true
    setTimeout(() => { saved.value = false }, 3000)
  } finally {
    saving.value = false
  }
}

onMounted(async () => { await settings.fetch(); syncForm() })
</script>
