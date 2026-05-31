<template>
  <div class="space-y-6">
    <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-5">
      <h2 class="font-semibold">Journaux</h2>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Niveau de log</label>
        <select v-model="form.log_level" class="field">
          <option value="DEBUG">DEBUG</option>
          <option value="INFO">INFO</option>
          <option value="WARNING">WARNING</option>
          <option value="ERROR">ERROR</option>
        </select>
      </div>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Scans parallèles max</label>
        <input v-model.number="form.max_parallel_library_scans" type="number" min="1" max="10" class="field" />
      </div>
    </section>

    <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-4">
      <h2 class="font-semibold">Planification</h2>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Intervalle scan (min)</label>
          <input v-model.number="form.scan_interval_minutes" type="number" min="10" class="field" />
        </div>
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Intervalle suppression (min)</label>
          <input v-model.number="form.deletion_check_interval_minutes" type="number" min="10" class="field" />
        </div>
      </div>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Rétention médias supprimés (jours)</label>
        <input v-model.number="form.deleted_retention_days" type="number" min="0" class="field" />
      </div>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Rétention journaux (jours)</label>
          <input v-model.number="form.log_retention_days" type="number" min="1" class="field" />
        </div>
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Rétention historique (jours)</label>
          <input v-model.number="form.job_history_retention_days" type="number" min="1" class="field" />
        </div>
      </div>
    </section>

    <section
      class="rounded-xl p-6 space-y-5 border transition-colors"
      :class="form.backup_enabled ? 'bg-green-500/5 border-green-500/30' : 'bg-red-500/5 border-red-500/30'"
    >
      <div class="flex items-center gap-2">
        <i :class="['fas', form.backup_enabled ? 'fa-shield-alt text-green-400' : 'fa-exclamation-triangle text-red-400']" />
        <h2 class="font-semibold">Sauvegarde de la base de données</h2>
      </div>
      <div class="flex items-center justify-between">
        <div>
          <div class="text-sm font-medium">Sauvegarde automatique</div>
          <div class="text-xs text-[var(--muted)]">Avant chaque cycle de suppression</div>
        </div>
        <ToggleSlider v-model="form.backup_enabled" />
      </div>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Intervalle (heures)</label>
          <input v-model.number="form.backup_interval_hours" type="number" min="1" class="field" />
        </div>
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Nombre de sauvegardes conservées</label>
          <input v-model.number="form.backup_retention_count" type="number" min="1" class="field" />
        </div>
      </div>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Dossier de sauvegarde</label>
        <input v-model="form.backup_path" type="text" placeholder="/app/data/backups" class="field" />
      </div>
      <div class="flex items-center gap-3">
        <button
          :disabled="backingUp"
          class="flex items-center gap-2 px-4 py-2 bg-[var(--bg3)] border border-[var(--border)] hover:border-[var(--accent)] rounded-lg text-sm transition-colors disabled:opacity-50"
          @click="triggerBackup"
        >
          <i class="fas fa-database text-xs" />
          {{ backingUp ? 'En cours…' : 'Sauvegarde manuelle' }}
        </button>
        <span v-if="backupMsg" class="text-xs" :class="backupOk ? 'text-green-400' : 'text-red-400'">{{ backupMsg }}</span>
      </div>
      <div v-if="backups.length" class="space-y-1">
        <div class="text-xs text-[var(--muted)] font-semibold uppercase tracking-wide mb-2">Sauvegardes existantes</div>
        <div v-for="b in backups" :key="b.filename" class="flex items-center justify-between text-xs px-3 py-1.5 bg-[var(--bg3)] rounded-lg group">
          <span class="font-mono flex-1 truncate">{{ b.filename }}</span>
          <span class="text-[var(--muted)] mr-3">{{ b.size_mb ? b.size_mb + ' MB' : '' }}</span>
          <button
            type="button"
            class="opacity-0 group-hover:opacity-100 transition-opacity text-red-400/70 hover:text-red-400 w-6 h-6 flex items-center justify-center rounded"
            title="Supprimer cette sauvegarde"
            @click.stop="deleteBackup(b.filename)"
          >
            <i class="fas fa-trash-can text-[10px]" />
          </button>
        </div>
      </div>
    </section>

    <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-5">
      <h2 class="font-semibold">Dashboard public</h2>
      <div class="flex items-center justify-between">
        <div>
          <div class="text-sm font-medium">Activer le dashboard public</div>
          <div class="text-xs text-[var(--muted)]">Page sans connexion avec le calendrier des suppressions</div>
        </div>
        <ToggleSlider v-model="form.public_dashboard_enabled" />
      </div>
      <template v-if="form.public_dashboard_enabled">
        <!-- Custom slug -->
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Segment d'URL (optionnel)</label>
          <div class="flex items-center gap-2">
            <span class="text-xs text-[var(--muted)] font-mono shrink-0">{{ origin }}/public/</span>
            <input
              v-model="form.public_dashboard_slug"
              type="text"
              placeholder="mon-calendrier"
              class="field flex-1 font-mono text-xs"
            />
          </div>
          <div class="mt-1 text-xs text-[var(--muted)]">
            URL complète :
            <code class="bg-[var(--bg3)] px-1.5 py-0.5 rounded">{{ publicUrl }}</code>
          </div>
        </div>
        <!-- Password -->
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Mot de passe (optionnel)</label>
          <div class="flex gap-2">
            <input
              v-model="form.public_dashboard_password"
              :type="showPwd ? 'text' : 'password'"
              placeholder="Laisser vide pour désactiver"
              class="field flex-1"
            />
            <button type="button" class="px-3 py-2 border border-[var(--border)] rounded-lg text-[var(--muted)] hover:text-white" @click="showPwd = !showPwd">
              <i :class="['fas', showPwd ? 'fa-eye-slash' : 'fa-eye', 'text-sm']" />
            </button>
          </div>
          <div class="mt-1 text-xs text-[var(--muted)]">Si défini, le visiteur devra entrer ce mot de passe pour accéder au dashboard.</div>
        </div>
      </template>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import ToggleSlider from '@/components/ui/ToggleSlider.vue'
import api from '@/api/client'

const props = defineProps({ form: { type: Object, required: true } })

const backingUp = ref(false)
const backupMsg = ref('')
const backupOk  = ref(false)
const backups   = ref([])
const showPwd   = ref(false)

const origin    = window.location.origin
const publicUrl = computed(() => {
  const slug = (props.form.public_dashboard_slug || '').trim()
  return slug ? `${origin}/public/${slug}` : `${origin}/public`
})

async function triggerBackup() {
  backingUp.value = true; backupMsg.value = ''; backupOk.value = false
  try {
    const { data } = await api.post('/backup')
    backupMsg.value = `Sauvegarde créée : ${data.filename}`
    backupOk.value  = true
    await loadBackups()
  } catch { backupMsg.value = 'Sauvegarde échouée'; backupOk.value = false }
  finally { backingUp.value = false }
}

async function loadBackups() {
  try { const { data } = await api.get('/backup'); backups.value = data || [] } catch { /* silent */ }
}

async function deleteBackup(filename) {
  try {
    await api.delete(`/backup/${encodeURIComponent(filename)}`)
    backups.value = backups.value.filter(b => b.filename !== filename)
  } catch { /* silent */ }
}

onMounted(loadBackups)
</script>

<style scoped>
.field {
  @apply w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)];
}
</style>
