# Frontend Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the 875-line `SettingsView.vue` monolith and 457-line `QueueView.vue` into focused, reusable single-responsibility components with no behavioral changes.

**Architecture:** Extract one logical unit per task — shared UI atoms first (`TestBtn`, `ConfirmModal`, `SortHeader`), then each settings tab as its own `.vue` file, leaving the parent view as a thin routing shell that owns `form` state and the `save()` function. `QueueView` gets its inline `defineComponent` replaced by proper `.vue` files.

**Tech Stack:** Vue 3 `<script setup>`, TailwindCSS CSS-variable design tokens, Vite 5 (build: `npm run build` in `frontend/vue/`), no test runner — correctness verified via build + visual check.

---

## File Structure

**New files to create:**
```
frontend/vue/src/components/ui/TestBtn.vue          ← extracted from SettingsView inline defineComponent
frontend/vue/src/components/ui/ConfirmModal.vue     ← extracted from QueueView Teleport modals
frontend/vue/src/components/ui/SortHeader.vue       ← extracted from QueueView inline defineComponent
frontend/vue/src/components/settings/GeneralTab.vue ← lines 27-141 of SettingsView + backup state
frontend/vue/src/components/settings/ServersTab.vue ← lines 144-284 of SettingsView + server state
frontend/vue/src/components/settings/RadarrTab.vue  ← lines 287-310 of SettingsView
frontend/vue/src/components/settings/SonarrTab.vue  ← lines 312-336 of SettingsView
frontend/vue/src/components/settings/SeerrTab.vue   ← lines 338-366 of SettingsView
frontend/vue/src/components/settings/QbitTab.vue    ← lines 368-460 of SettingsView
frontend/vue/src/components/settings/DiscordTab.vue ← lines 462-517 of SettingsView + AlertRow
```

**Files to modify:**
```
frontend/vue/src/views/SettingsView.vue  ← becomes ~100-line shell (tab bar + save button + form state)
frontend/vue/src/views/QueueView.vue     ← uses extracted components, removes inline defineComponents
```

**Invariants to maintain:**
- `form` reactive object stays in `SettingsView.vue` — tabs receive it as a prop and mutate its fields directly (object mutation through props is fine in Vue 3)
- `save()` stays in `SettingsView.vue` — serializes all `form` fields to the settings API
- The public export of `run_scan`, `run_scan_library`, `reevaluate_library_queue` from `scheduler.py` is unchanged

---

### Task 1: Extract TestBtn.vue

**Files:**
- Create: `frontend/vue/src/components/ui/TestBtn.vue`

Currently an inline `defineComponent` using `h()` inside `SettingsView.vue`. Multiple tabs need it — it must be a first-class component.

- [ ] **Step 1: Create the component**

```vue
<!-- frontend/vue/src/components/ui/TestBtn.vue -->
<template>
  <button
    @click="test"
    class="text-xs px-3 py-1.5 rounded-lg border transition-colors whitespace-nowrap"
    :class="state === 'ok'    ? 'border-green-500/50 text-green-400' :
            state === 'error' ? 'border-red-500/50 text-red-400' :
            'border-[var(--border)] text-[var(--muted)] hover:text-white'"
  >
    {{ state === 'loading' ? '…' : state === 'ok' ? '✓ OK' : state === 'error' ? '✗ Erreur' : 'Tester' }}
  </button>
</template>

<script setup>
import { ref } from 'vue'
import api from '@/api/client'

const props = defineProps({ service: { type: String, required: true } })
const state = ref('idle')

async function test() {
  state.value = 'loading'
  try {
    const { data } = await api.post(`/settings/test/${props.service}`)
    state.value = data.ok ? 'ok' : 'error'
  } catch {
    state.value = 'error'
  }
  setTimeout(() => { state.value = 'idle' }, 4000)
}
</script>
```

- [ ] **Step 2: Verify build passes**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | tail -5
```
Expected: `✓ built in Xs`

- [ ] **Step 3: Commit**

```bash
git -C /opt/claude/hygie add frontend/vue/src/components/ui/TestBtn.vue
git -C /opt/claude/hygie commit -m "feat(frontend): extract TestBtn.vue reusable component"
```

---

### Task 2: Extract ConfirmModal.vue

**Files:**
- Create: `frontend/vue/src/components/ui/ConfirmModal.vue`

QueueView has three nearly-identical `<Teleport>` confirm modals (ignore, delete, purge). This component unifies them with a default slot for custom body content.

- [ ] **Step 1: Create the component**

```vue
<!-- frontend/vue/src/components/ui/ConfirmModal.vue -->
<template>
  <Teleport to="body">
    <div
      v-if="show"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      @mousedown.self="$emit('cancel')"
    >
      <div class="bg-[var(--bg1)] border border-[var(--border)] rounded-2xl p-6 w-full max-w-sm shadow-2xl space-y-4">
        <slot>
          <p class="text-sm">{{ message }}</p>
        </slot>
        <div class="flex justify-end gap-3">
          <button
            class="px-4 py-2 text-sm text-[var(--muted)] hover:text-[var(--text)] transition-colors"
            @click="$emit('cancel')"
          >{{ cancelLabel }}</button>
          <button
            class="px-4 py-2 text-sm rounded-lg transition-colors"
            :class="confirmClass"
            @click="$emit('confirm')"
          >{{ confirmLabel }}</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
defineProps({
  show:         { type: Boolean, required: true },
  message:      { type: String,  default: '' },
  confirmLabel: { type: String,  default: 'Confirmer' },
  cancelLabel:  { type: String,  default: 'Annuler' },
  confirmClass: { type: String,  default: 'bg-red-500 hover:bg-red-600 text-white' },
})
defineEmits(['confirm', 'cancel'])
</script>
```

- [ ] **Step 2: Verify build passes**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git -C /opt/claude/hygie add frontend/vue/src/components/ui/ConfirmModal.vue
git -C /opt/claude/hygie commit -m "feat(frontend): extract ConfirmModal.vue reusable component"
```

---

### Task 3: Extract SortHeader.vue

**Files:**
- Create: `frontend/vue/src/components/ui/SortHeader.vue`

Currently an inline `defineComponent` using `h()` inside QueueView. Replacing it with a proper `.vue` component removes the h() boilerplate and makes it importable everywhere.

- [ ] **Step 1: Create the component**

```vue
<!-- frontend/vue/src/components/ui/SortHeader.vue -->
<template>
  <button
    class="flex items-center gap-1 text-xs hover:text-white transition-colors group"
    @click="$emit('sort', field)"
  >
    <span>{{ label }}</span>
    <i
      :class="[
        'fas text-[10px] transition-colors',
        sort === field
          ? (dir === 'asc' ? 'fa-sort-up text-[var(--accent)]' : 'fa-sort-down text-[var(--accent)]')
          : 'fa-sort opacity-30 group-hover:opacity-60',
      ]"
    />
  </button>
</template>

<script setup>
defineProps({
  label: { type: String, required: true },
  field: { type: String, required: true },
  sort:  { type: String, required: true },
  dir:   { type: String, required: true },
})
defineEmits(['sort'])
</script>
```

- [ ] **Step 2: Verify build passes**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git -C /opt/claude/hygie add frontend/vue/src/components/ui/SortHeader.vue
git -C /opt/claude/hygie commit -m "feat(frontend): extract SortHeader.vue reusable component"
```

---

### Task 4: Extract GeneralTab.vue

**Files:**
- Create: `frontend/vue/src/components/settings/GeneralTab.vue`

Owns: dry_run, log_level, scan intervals, backup toggle/config/trigger/list, public dashboard.
Local state: `backingUp`, `backupMsg`, `backupOk`, `backups` — these never leave this tab.

- [ ] **Step 1: Create the component**

```vue
<!-- frontend/vue/src/components/settings/GeneralTab.vue -->
<template>
  <div class="space-y-6">
    <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-5">
      <h2 class="font-semibold">Mode de fonctionnement</h2>
      <div class="flex items-center justify-between">
        <div>
          <div class="text-sm font-medium">Mode Dry Run</div>
          <div class="text-xs text-[var(--muted)]">Simule les suppressions sans les exécuter</div>
        </div>
        <ToggleSlider v-model="form.dry_run" />
      </div>
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
          @click="triggerBackup"
          :disabled="backingUp"
          class="flex items-center gap-2 px-4 py-2 bg-[var(--bg3)] border border-[var(--border)] hover:border-[var(--accent)] rounded-lg text-sm transition-colors disabled:opacity-50"
        >
          <i class="fas fa-database text-xs" />
          {{ backingUp ? 'En cours…' : 'Sauvegarde manuelle' }}
        </button>
        <span v-if="backupMsg" class="text-xs" :class="backupOk ? 'text-green-400' : 'text-red-400'">{{ backupMsg }}</span>
      </div>
      <div v-if="backups.length" class="space-y-1">
        <div class="text-xs text-[var(--muted)] font-semibold uppercase tracking-wide mb-2">Sauvegardes existantes</div>
        <div v-for="b in backups" :key="b.filename" class="flex items-center justify-between text-xs px-3 py-1.5 bg-[var(--bg3)] rounded-lg">
          <span class="font-mono">{{ b.filename }}</span>
          <span class="text-[var(--muted)]">{{ b.size_mb ? b.size_mb + ' MB' : '' }}</span>
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
      <div v-if="form.public_dashboard_enabled" class="text-xs text-[var(--muted)]">
        URL : <code class="bg-[var(--bg3)] px-1.5 py-0.5 rounded">{{ publicUrl }}</code>
      </div>
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

const publicUrl = computed(() => `${window.location.origin}/public`)

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

onMounted(loadBackups)
</script>

<style scoped>
.field {
  @apply w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)];
}
</style>
```

- [ ] **Step 2: Verify build passes**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git -C /opt/claude/hygie add frontend/vue/src/components/settings/GeneralTab.vue
git -C /opt/claude/hygie commit -m "feat(frontend): extract GeneralTab.vue settings component"
```

---

### Task 5: Extract ServersTab.vue

**Files:**
- Create: `frontend/vue/src/components/settings/ServersTab.vue`

Owns: media server CRUD list, Emby collection config, Plex token/webhook config. All server-related state lives here.

- [ ] **Step 1: Create the component**

```vue
<!-- frontend/vue/src/components/settings/ServersTab.vue -->
<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <div class="text-sm text-[var(--muted)]">Gérez vos serveurs Emby, Jellyfin et Plex</div>
      <button @click="addServer" class="flex items-center gap-1.5 px-3 py-2 bg-[var(--accent)] hover:opacity-90 rounded-lg text-sm transition-opacity">
        <i class="fas fa-plus text-xs" /> Ajouter un serveur
      </button>
    </div>

    <div v-if="!mediaServers.length" class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-8 text-center text-[var(--muted)] text-sm">
      <i class="fas fa-server text-3xl mb-3 block opacity-30" />
      Aucun serveur configuré.
    </div>

    <div v-for="(srv, idx) in mediaServers" :key="srv._uid" class="bg-[var(--bg2)] border rounded-xl overflow-hidden" :class="serverBorderClass(srv.type)">
      <div class="flex items-center justify-between px-5 py-3 border-b border-[var(--border)]" :class="serverHeaderClass(srv.type)">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-lg flex items-center justify-center bg-black/20">
            <ServiceIcon v-if="serverService(srv.type)" :name="serverService(srv.type)" :size="22" />
            <i v-else class="fas fa-server text-[var(--muted)] text-sm" />
          </div>
          <div>
            <div class="text-sm font-semibold">{{ srv.name || 'Serveur sans nom' }}</div>
            <div class="text-xs opacity-70 capitalize">{{ srv.type || 'type inconnu' }}</div>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <button @click="testServer(srv)"
            class="text-xs px-3 py-1.5 rounded-lg border transition-colors"
            :class="srv._testOk === true ? 'border-green-500/50 text-green-400' : srv._testOk === false ? 'border-red-500/50 text-red-400' : 'border-[var(--border)] text-[var(--muted)] hover:text-white'">
            {{ srv._testing ? '…' : srv._testOk === true ? '✓ OK' : srv._testOk === false ? '✗ Erreur' : 'Tester' }}
          </button>
          <button @click="removeServer(idx)" class="text-[var(--muted)] hover:text-red-400 transition-colors">
            <i class="fas fa-trash text-sm" />
          </button>
        </div>
      </div>
      <div class="p-5 grid grid-cols-1 gap-4">
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-xs text-[var(--muted)] mb-1">Nom</label>
            <input v-model="srv.name" type="text" placeholder="Mon serveur" class="field" />
          </div>
          <div>
            <label class="block text-xs text-[var(--muted)] mb-1">Type</label>
            <select v-model="srv.type" class="field">
              <option value="">— Sélectionner —</option>
              <option value="emby">Emby</option>
              <option value="jellyfin">Jellyfin</option>
              <option value="plex">Plex</option>
            </select>
          </div>
        </div>
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">URL locale</label>
          <input v-model="srv.url" type="url" placeholder="http://192.168.1.10:8096" class="field font-mono" />
        </div>
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">Clé API</label>
          <div class="flex gap-2">
            <input v-model="srv.api_key" :type="srv._showKey ? 'text' : 'password'" placeholder="••••••••" class="flex-1 field font-mono" />
            <button type="button" class="px-3 py-2 border border-[var(--border)] rounded-lg text-[var(--muted)] hover:text-white" @click="srv._showKey = !srv._showKey">
              <i :class="['fas', srv._showKey ? 'fa-eye-slash' : 'fa-eye', 'text-sm']" />
            </button>
          </div>
        </div>
        <div>
          <label class="block text-xs text-[var(--muted)] mb-1">URL externe (optionnel)</label>
          <input v-model="srv.ext_url" type="url" placeholder="https://emby.mondomaine.fr" class="field font-mono" />
        </div>
        <div class="flex items-center justify-between">
          <div class="text-sm">Activé</div>
          <ToggleSlider v-model="srv.enabled" />
        </div>
      </div>
    </div>

    <!-- Emby collection -->
    <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-4">
      <h2 class="font-semibold">Collection Emby « Bientôt supprimé »</h2>
      <div class="flex items-center justify-between">
        <div>
          <div class="text-sm font-medium">Overlay « Supprimé dans Xj »</div>
          <div class="text-xs text-[var(--muted)]">Bannière sur les affiches Emby</div>
        </div>
        <ToggleSlider v-model="form.emby_leaving_soon_overlay" />
      </div>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Nom de la collection</label>
        <input v-model="form.emby_leaving_soon_collection" type="text" placeholder="Bientôt supprimé" class="field" />
      </div>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Seuil en jours</label>
        <input v-model.number="form.emby_leaving_soon_days" type="number" min="1" class="field" />
      </div>
    </section>

    <!-- Plex -->
    <section class="bg-[var(--bg2)] border border-orange-500/20 rounded-xl p-6 space-y-4">
      <div class="flex items-center gap-3">
        <div class="w-8 h-8 rounded-lg bg-[#E5A00D]/20 flex items-center justify-center">
          <ServiceIcon name="plex" :size="18" />
        </div>
        <h2 class="font-semibold">Plex</h2>
      </div>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Token Plex.tv</label>
        <div class="flex gap-2">
          <input v-model="form.plex_tv_token" :type="showPlexToken ? 'text' : 'password'" placeholder="Token…" class="flex-1 field font-mono" />
          <button type="button" class="px-3 py-2 border border-[var(--border)] rounded-lg text-[var(--muted)] hover:text-white" @click="showPlexToken = !showPlexToken">
            <i :class="['fas', showPlexToken ? 'fa-eye-slash' : 'fa-eye', 'text-sm']" />
          </button>
        </div>
      </div>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Secret Webhook</label>
        <div class="flex gap-2">
          <input v-model="form.plex_webhook_secret" :type="showWebhookSecret ? 'text' : 'password'" placeholder="Secret optionnel…" class="flex-1 field font-mono" />
          <button type="button" class="px-3 py-2 border border-[var(--border)] rounded-lg text-[var(--muted)] hover:text-white" @click="showWebhookSecret = !showWebhookSecret">
            <i :class="['fas', showWebhookSecret ? 'fa-eye-slash' : 'fa-eye', 'text-sm']" />
          </button>
        </div>
        <p class="text-xs text-[var(--muted)] mt-1">URL webhook : <code class="bg-[var(--bg3)] px-1 rounded">{{ webhookUrl }}</code></p>
      </div>
      <div class="flex items-center justify-between">
        <div>
          <div class="text-sm font-medium">Overlay affiches Plex</div>
          <div class="text-xs text-[var(--muted)]">Bannière « Supprimé dans Xj » sur Plex</div>
        </div>
        <ToggleSlider v-model="form.plex_overlay_enabled" />
      </div>
    </section>

    <button @click="saveServers" :disabled="savingServers"
      class="w-full bg-[var(--bg3)] hover:bg-[var(--border)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm transition-colors disabled:opacity-50">
      {{ savingServers ? 'Enregistrement…' : 'Sauvegarder les serveurs' }}
    </button>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import ToggleSlider from '@/components/ui/ToggleSlider.vue'
import ServiceIcon from '@/components/ui/ServiceIcon.vue'
import { useSettingsStore } from '@/stores/settings'
import api from '@/api/client'

const props = defineProps({ form: { type: Object, required: true } })

const settings = useSettingsStore()

const showPlexToken     = ref(false)
const showWebhookSecret = ref(false)
const mediaServers      = ref([])
const savingServers     = ref(false)
let   _uid = 0

const webhookUrl = computed(() => {
  const secret = props.form.plex_webhook_secret
  return secret ? `${window.location.origin}/api/plex/webhook?secret=${secret}` : `${window.location.origin}/api/plex/webhook`
})

const SERVER_CONFIG = {
  emby:     { service: 'emby',     border: 'border-green-600/30',  header: 'bg-green-600/10' },
  jellyfin: { service: 'jellyfin', border: 'border-blue-500/30',   header: 'bg-blue-500/10' },
  plex:     { service: 'plex',     border: 'border-yellow-500/30', header: 'bg-yellow-500/10' },
}
const DEF = { service: null, border: 'border-[var(--border)]', header: '' }

function serverService(type)     { return (SERVER_CONFIG[type] || DEF).service }
function serverBorderClass(type) { return (SERVER_CONFIG[type] || DEF).border }
function serverHeaderClass(type) { return (SERVER_CONFIG[type] || DEF).header }

function addServer() {
  mediaServers.value.push({ _uid: ++_uid, id: null, name: '', url: '', api_key: '', ext_url: '', type: '', enabled: true, _showKey: true, _testing: false, _testOk: null })
}
function removeServer(idx) { mediaServers.value.splice(idx, 1) }

async function testServer(srv) {
  if (!srv.id) return
  srv._testing = true; srv._testOk = null
  try {
    const { data } = await api.post(`/settings/media-servers/${srv.id}/test`)
    srv._testOk = data.ok
    if (data.server_type) srv.type = data.server_type
  } catch { srv._testOk = false }
  finally { srv._testing = false }
}

const _detectTimers = new Map()
function scheduleAutoDetect(srv) {
  if (!srv.id) return
  if (_detectTimers.has(srv._uid)) clearTimeout(_detectTimers.get(srv._uid))
  _detectTimers.set(srv._uid, setTimeout(async () => {
    if (!srv.url) return
    try {
      const { data } = await api.post(`/settings/media-servers/${srv.id}/test`)
      if (data.server_type && data.server_type !== 'unknown') srv.type = data.server_type
    } catch { /* silent */ }
  }, 800))
}

watch(
  () => mediaServers.value.map(s => s.url),
  (urls, prev) => {
    if (!prev) return
    urls.forEach((url, i) => { if (url !== prev[i]) scheduleAutoDetect(mediaServers.value[i]) })
  }
)

async function loadServers() {
  try {
    const { data } = await api.get('/settings/media-servers')
    mediaServers.value = (data || []).map(s => ({ ...s, _uid: ++_uid, _showKey: false, _testing: false, _testOk: null }))
  } catch { /* silent */ }
}

async function saveServers() {
  savingServers.value = true
  try {
    const current = (await api.get('/settings/media-servers')).data || []
    const currentIds = new Set(current.map(s => String(s.id)))
    for (const srv of mediaServers.value) {
      const { _uid, _showKey, _testing, _testOk, ...payload } = srv
      if (!payload.id) {
        const { data } = await api.post('/settings/media-servers', payload)
        srv.id = data.id
      } else {
        await api.put(`/settings/media-servers/${payload.id}`, payload)
        currentIds.delete(String(payload.id))
      }
    }
    for (const id of currentIds) await api.delete(`/settings/media-servers/${id}`)
    await loadServers()
    const f = props.form
    await settings.save({
      emby_leaving_soon_overlay:    String(f.emby_leaving_soon_overlay),
      emby_leaving_soon_collection: f.emby_leaving_soon_collection,
      emby_leaving_soon_days:       String(f.emby_leaving_soon_days),
      plex_tv_token:                f.plex_tv_token,
      plex_webhook_secret:          f.plex_webhook_secret,
      plex_overlay_enabled:         String(f.plex_overlay_enabled),
    })
  } finally { savingServers.value = false }
}

loadServers()
</script>

<style scoped>
.field {
  @apply w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)];
}
</style>
```

- [ ] **Step 2: Verify build passes**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git -C /opt/claude/hygie add frontend/vue/src/components/settings/ServersTab.vue
git -C /opt/claude/hygie commit -m "feat(frontend): extract ServersTab.vue settings component"
```

---

### Task 6: Extract RadarrTab, SonarrTab, SeerrTab

**Files:**
- Create: `frontend/vue/src/components/settings/RadarrTab.vue`
- Create: `frontend/vue/src/components/settings/SonarrTab.vue`
- Create: `frontend/vue/src/components/settings/SeerrTab.vue`

Three small tabs, one commit.

- [ ] **Step 1: Create RadarrTab.vue**

```vue
<!-- frontend/vue/src/components/settings/RadarrTab.vue -->
<template>
  <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-4">
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-lg bg-[#FFBE00]/20 flex items-center justify-center">
          <ServiceIcon name="radarr" :size="22" />
        </div>
        <h2 class="font-semibold">Radarr</h2>
      </div>
      <TestBtn service="radarr" />
    </div>
    <div>
      <label class="block text-xs text-[var(--muted)] mb-1">URL</label>
      <input v-model="form.radarr_url" type="url" placeholder="http://radarr:7878" class="field font-mono" />
    </div>
    <div>
      <label class="block text-xs text-[var(--muted)] mb-1">Clé API</label>
      <div class="flex gap-2">
        <input v-model="form.radarr_api_key" :type="showKey ? 'text' : 'password'" placeholder="••••••••" class="flex-1 field font-mono" />
        <button type="button" class="px-3 py-2 border border-[var(--border)] rounded-lg text-[var(--muted)] hover:text-white" @click="showKey = !showKey">
          <i :class="['fas', showKey ? 'fa-eye-slash' : 'fa-eye', 'text-sm']" />
        </button>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref } from 'vue'
import ServiceIcon from '@/components/ui/ServiceIcon.vue'
import TestBtn from '@/components/ui/TestBtn.vue'

defineProps({ form: { type: Object, required: true } })
const showKey = ref(false)
</script>

<style scoped>
.field { @apply w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]; }
</style>
```

- [ ] **Step 2: Create SonarrTab.vue**

```vue
<!-- frontend/vue/src/components/settings/SonarrTab.vue -->
<template>
  <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-4">
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-lg bg-[#35C5F4]/20 flex items-center justify-center">
          <ServiceIcon name="sonarr" :size="22" />
        </div>
        <h2 class="font-semibold">Sonarr</h2>
      </div>
      <TestBtn service="sonarr" />
    </div>
    <div>
      <label class="block text-xs text-[var(--muted)] mb-1">URL</label>
      <input v-model="form.sonarr_url" type="url" placeholder="http://sonarr:8989" class="field font-mono" />
    </div>
    <div>
      <label class="block text-xs text-[var(--muted)] mb-1">Clé API</label>
      <div class="flex gap-2">
        <input v-model="form.sonarr_api_key" :type="showKey ? 'text' : 'password'" placeholder="••••••••" class="flex-1 field font-mono" />
        <button type="button" class="px-3 py-2 border border-[var(--border)] rounded-lg text-[var(--muted)] hover:text-white" @click="showKey = !showKey">
          <i :class="['fas', showKey ? 'fa-eye-slash' : 'fa-eye', 'text-sm']" />
        </button>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref } from 'vue'
import ServiceIcon from '@/components/ui/ServiceIcon.vue'
import TestBtn from '@/components/ui/TestBtn.vue'

defineProps({ form: { type: Object, required: true } })
const showKey = ref(false)
</script>

<style scoped>
.field { @apply w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]; }
</style>
```

- [ ] **Step 3: Create SeerrTab.vue**

```vue
<!-- frontend/vue/src/components/settings/SeerrTab.vue -->
<template>
  <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-4">
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-lg bg-[#8B5CF6]/20 flex items-center justify-center">
          <ServiceIcon name="overseerr" :size="22" />
        </div>
        <h2 class="font-semibold">Overseerr / Jellyseerr</h2>
      </div>
      <TestBtn service="seerr" />
    </div>
    <div>
      <label class="block text-xs text-[var(--muted)] mb-1">URL</label>
      <input v-model="form.seerr_url" type="url" placeholder="http://seerr:5055" class="field font-mono" />
    </div>
    <div>
      <label class="block text-xs text-[var(--muted)] mb-1">Clé API</label>
      <div class="flex gap-2">
        <input v-model="form.seerr_api_key" :type="showKey ? 'text' : 'password'" placeholder="••••••••" class="flex-1 field font-mono" />
        <button type="button" class="px-3 py-2 border border-[var(--border)] rounded-lg text-[var(--muted)] hover:text-white" @click="showKey = !showKey">
          <i :class="['fas', showKey ? 'fa-eye-slash' : 'fa-eye', 'text-sm']" />
        </button>
      </div>
    </div>
    <div>
      <label class="block text-xs text-[var(--muted)] mb-1">URL externe (liens dans les notifications)</label>
      <input v-model="form.seerr_external_url" type="url" placeholder="https://seerr.mondomaine.fr" class="field font-mono" />
    </div>
  </section>
</template>

<script setup>
import { ref } from 'vue'
import ServiceIcon from '@/components/ui/ServiceIcon.vue'
import TestBtn from '@/components/ui/TestBtn.vue'

defineProps({ form: { type: Object, required: true } })
const showKey = ref(false)
</script>

<style scoped>
.field { @apply w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]; }
</style>
```

- [ ] **Step 4: Verify build passes**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git -C /opt/claude/hygie add frontend/vue/src/components/settings/RadarrTab.vue \
    frontend/vue/src/components/settings/SonarrTab.vue \
    frontend/vue/src/components/settings/SeerrTab.vue
git -C /opt/claude/hygie commit -m "feat(frontend): extract RadarrTab, SonarrTab, SeerrTab components"
```

---

### Task 7: Extract QbitTab.vue

**Files:**
- Create: `frontend/vue/src/components/settings/QbitTab.vue`

Owns qBittorrent URL + Qui proxy URL with inline test buttons, credentials, action sliders, tag toggle.

- [ ] **Step 1: Create the component**

```vue
<!-- frontend/vue/src/components/settings/QbitTab.vue -->
<template>
  <section class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-6 space-y-4">
    <div class="flex items-center gap-3">
      <div class="w-9 h-9 rounded-lg bg-[#2F67BA]/20 flex items-center justify-center">
        <ServiceIcon name="qbittorrent" :size="18" />
      </div>
      <h2 class="font-semibold">qBittorrent</h2>
    </div>

    <div>
      <label class="block text-xs text-[var(--muted)] mb-1">URL qBittorrent</label>
      <div class="flex gap-2">
        <input v-model="form.qbit_url" type="url" placeholder="http://qbittorrent:8080" class="flex-1 field font-mono" />
        <TestBtn service="qbit" />
      </div>
    </div>

    <div class="grid grid-cols-2 gap-4">
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Utilisateur</label>
        <input v-model="form.qbit_user" type="text" class="field" />
      </div>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Mot de passe</label>
        <div class="flex gap-2">
          <input v-model="form.qbit_password" :type="showPwd ? 'text' : 'password'" class="flex-1 field" />
          <button type="button" class="px-3 py-2 border border-[var(--border)] rounded-lg text-[var(--muted)] hover:text-white" @click="showPwd = !showPwd">
            <i :class="['fas', showPwd ? 'fa-eye-slash' : 'fa-eye', 'text-sm']" />
          </button>
        </div>
      </div>
    </div>

    <div class="flex items-center gap-3 py-1">
      <div class="flex-1 h-px bg-[var(--border)]" />
      <div class="flex items-center gap-2 px-3 py-1.5 rounded-full bg-[var(--bg3)] border border-[var(--border)] text-xs text-[var(--muted)]">
        <span>ET/OU</span>
        <ServiceIcon name="qui" :size="13" />
        <span class="font-medium text-[var(--text)]">URL proxy QUI</span>
      </div>
      <div class="flex-1 h-px bg-[var(--border)]" />
    </div>

    <div>
      <label class="block text-xs text-[var(--muted)] mb-1">URL proxy QUI <span class="font-normal">(optionnel)</span></label>
      <div class="flex gap-2">
        <input v-model="form.qbit_proxy_url" type="url" placeholder="http://qui:3000" class="flex-1 field font-mono" />
        <TestBtn v-if="form.qbit_proxy_url" service="qui" />
      </div>
    </div>

    <div>
      <label class="block text-xs text-[var(--muted)] mb-2">Actions lors d'une suppression</label>
      <div class="space-y-2">
        <div class="flex items-center justify-between py-2 px-3 bg-[var(--bg3)] rounded-lg">
          <span class="text-sm">Mettre en pause le torrent</span>
          <ToggleSlider :model-value="form.qbit_action === 'pause'" @update:model-value="form.qbit_action = $event ? 'pause' : ''" />
        </div>
        <div class="flex items-center justify-between py-2 px-3 bg-[var(--bg3)] rounded-lg">
          <span class="text-sm">Supprimer le torrent <span class="text-[var(--muted)] text-xs">(sans fichiers)</span></span>
          <ToggleSlider :model-value="form.qbit_action === 'delete_torrent'" @update:model-value="form.qbit_action = $event ? 'delete_torrent' : ''" />
        </div>
        <div class="flex items-center justify-between py-2 px-3 bg-[var(--bg3)] rounded-lg">
          <span class="text-sm">Supprimer le torrent <span class="text-red-400 text-xs font-medium">+ fichiers</span></span>
          <ToggleSlider :model-value="form.qbit_action === 'delete_files'" @update:model-value="form.qbit_action = $event ? 'delete_files' : ''" />
        </div>
      </div>
    </div>

    <div>
      <div class="flex items-center justify-between py-2 px-3 bg-[var(--bg3)] rounded-lg">
        <span class="text-sm">Appliquer un tag au torrent</span>
        <ToggleSlider :model-value="tagEnabled" @update:model-value="onTagToggle" />
      </div>
      <div v-if="tagEnabled" class="mt-2">
        <input v-model="form.qbit_tag" type="text" placeholder="hygie-deleted" class="field" ref="tagInput" />
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, nextTick } from 'vue'
import ToggleSlider from '@/components/ui/ToggleSlider.vue'
import ServiceIcon from '@/components/ui/ServiceIcon.vue'
import TestBtn from '@/components/ui/TestBtn.vue'

const props = defineProps({ form: { type: Object, required: true } })
const showPwd  = ref(false)
const tagInput = ref(null)

const tagEnabled = computed(() => !!props.form.qbit_tag)

async function onTagToggle(enabled) {
  if (!enabled) {
    props.form.qbit_tag = ''
  } else {
    props.form.qbit_tag = props.form.qbit_tag || 'hygie-deleted'
    await nextTick()
    tagInput.value?.focus()
  }
}
</script>

<style scoped>
.field { @apply w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]; }
</style>
```

- [ ] **Step 2: Verify build passes**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git -C /opt/claude/hygie add frontend/vue/src/components/settings/QbitTab.vue
git -C /opt/claude/hygie commit -m "feat(frontend): extract QbitTab.vue settings component"
```

---

### Task 8: Extract DiscordTab.vue

**Files:**
- Create: `frontend/vue/src/components/settings/DiscordTab.vue`

Moves the Discord section + the `AlertRow` inline component into one self-contained file. `AlertRow` becomes a proper `<script setup>` component defined locally via a child component import.

- [ ] **Step 1: Create the component**

```vue
<!-- frontend/vue/src/components/settings/DiscordTab.vue -->
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
          label="Erreur de suppression"
          v-model:enabled="form.discord_alert_deletion_error"
          v-model:mention="form.discord_alert_deletion_error_mention"
          v-model:msg="form.discord_alert_deletion_error_msg"
        />
        <AlertRow
          label="Échec de scan"
          v-model:enabled="form.discord_alert_scan_failure"
          v-model:mention="form.discord_alert_scan_failure_mention"
          v-model:msg="form.discord_alert_scan_failure_msg"
        />
        <AlertRow
          label="Échec Seerr"
          v-model:enabled="form.discord_alert_seerr_failure"
          v-model:mention="form.discord_alert_seerr_failure_mention"
          v-model:msg="form.discord_alert_seerr_failure_msg"
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
import { ref, computed } from 'vue'
import ServiceIcon from '@/components/ui/ServiceIcon.vue'
import TestBtn from '@/components/ui/TestBtn.vue'
import ToggleSlider from '@/components/ui/ToggleSlider.vue'

defineProps({ form: { type: Object, required: true } })

const showWebhook = ref(false)
const showAlerts  = ref(false)

// AlertRow — scoped sub-component (inline template approach)
const AlertRow = {
  props: { label: String, enabled: String, mention: String, msg: String },
  emits: ['update:enabled', 'update:mention', 'update:msg'],
  components: { ToggleSlider },
  computed: {
    isEnabled: {
      get() { return this.enabled === 'true' || this.enabled === true },
      set(v)  { this.$emit('update:enabled', String(v)) },
    },
  },
  template: `
    <div class="space-y-2">
      <div class="flex items-center justify-between py-2 px-3 bg-[var(--bg3)] rounded-lg">
        <span class="text-sm">{{ label }}</span>
        <ToggleSlider :model-value="isEnabled" @update:model-value="v => { isEnabled = v }" />
      </div>
      <div v-if="isEnabled" class="grid grid-cols-2 gap-2 px-1">
        <input type="text" placeholder="Mention (@role / @user)" :value="mention || ''" @input="$emit('update:mention', $event.target.value)" class="field text-xs" />
        <input type="text" placeholder="Message personnalisé" :value="msg || ''" @input="$emit('update:msg', $event.target.value)" class="field text-xs" />
      </div>
    </div>
  `,
}
</script>

<style scoped>
.field { @apply w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]; }
</style>
```

- [ ] **Step 2: Verify build passes**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git -C /opt/claude/hygie add frontend/vue/src/components/settings/DiscordTab.vue
git -C /opt/claude/hygie commit -m "feat(frontend): extract DiscordTab.vue settings component"
```

---

### Task 9: Slim SettingsView.vue to shell-only

**Files:**
- Modify: `frontend/vue/src/views/SettingsView.vue`

Replace the entire file with the tab-routing shell. All section templates are gone — replaced by `<XxxTab :form="form" />`. The `form` ref, `syncForm()`, `save()`, and `TABS` computed stay here.

- [ ] **Step 1: Replace SettingsView.vue**

```vue
<!-- frontend/vue/src/views/SettingsView.vue -->
<template>
  <div class="space-y-6">
    <!-- Tab bar -->
    <div class="flex flex-wrap gap-1 bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-1">
      <button
        v-for="tab in TABS"
        :key="tab.id"
        @click="activeTab = tab.id"
        class="flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors"
        :class="activeTab === tab.id ? 'bg-[var(--accent)] text-white' : 'text-[var(--muted)] hover:text-white hover:bg-[var(--bg3)]'"
      >
        <ServiceIcon v-if="tab.service" :name="tab.service" :size="14" :color="activeTab === tab.id ? '#fff' : undefined" />
        <i v-else :class="['fas', tab.faIcon, 'text-xs']" />
        <span>{{ tab.label }}</span>
      </button>
    </div>

    <div v-if="saved" role="status" class="bg-green-500/20 border border-green-500/30 text-green-400 rounded-lg px-4 py-3 text-sm flex items-center gap-2">
      <i class="fas fa-check-circle" /> Paramètres sauvegardés.
    </div>
    <div v-if="saveError" class="bg-red-500/20 border border-red-500/30 text-red-400 rounded-lg px-4 py-3 text-sm flex items-center gap-2">
      <i class="fas fa-exclamation-triangle" /> {{ saveError }}
    </div>

    <!-- Tab content -->
    <GeneralTab  v-if="activeTab === 'general'"  :form="form" />
    <ServersTab  v-else-if="activeTab === 'servers'"  :form="form" />
    <RadarrTab   v-else-if="activeTab === 'radarr'"   :form="form" />
    <SonarrTab   v-else-if="activeTab === 'sonarr'"   :form="form" />
    <SeerrTab    v-else-if="activeTab === 'seerr'"    :form="form" />
    <QbitTab     v-else-if="activeTab === 'qbit'"     :form="form" />
    <DiscordTab  v-else-if="activeTab === 'discord'"  :form="form" />

    <!-- Save button (all tabs except servers) -->
    <button
      v-if="activeTab !== 'servers'"
      @click="save"
      :disabled="saving"
      class="w-full bg-[var(--accent)] hover:opacity-90 disabled:opacity-50 rounded-lg px-6 py-3 text-sm font-semibold transition-opacity"
    >
      {{ saving ? 'Enregistrement…' : 'Enregistrer' }}
    </button>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useSettingsStore } from '@/stores/settings'
import ServiceIcon from '@/components/ui/ServiceIcon.vue'
import GeneralTab  from '@/components/settings/GeneralTab.vue'
import ServersTab  from '@/components/settings/ServersTab.vue'
import RadarrTab   from '@/components/settings/RadarrTab.vue'
import SonarrTab   from '@/components/settings/SonarrTab.vue'
import SeerrTab    from '@/components/settings/SeerrTab.vue'
import QbitTab     from '@/components/settings/QbitTab.vue'
import DiscordTab  from '@/components/settings/DiscordTab.vue'

const settings  = useSettingsStore()
const saving    = ref(false)
const saved     = ref(false)
const saveError = ref('')
const activeTab = ref('general')

const form = ref({})

const TABS = computed(() => [
  { id: 'general',  faIcon: 'fa-cog',    label: 'Général',     service: null },
  { id: 'servers',  faIcon: 'fa-server', label: 'Serveurs',    service: null },
  { id: 'radarr',   faIcon: null,        label: 'Radarr',      service: 'radarr' },
  { id: 'sonarr',   faIcon: null,        label: 'Sonarr',      service: 'sonarr' },
  { id: 'seerr',    faIcon: null,        label: 'Seerr',       service: 'overseerr' },
  { id: 'qbit',     faIcon: null,        label: 'qBittorrent', service: form.value.qbit_proxy_url ? 'qui' : 'qbittorrent' },
  { id: 'discord',  faIcon: null,        label: 'Discord',     service: 'discord' },
])

function syncForm() {
  const s = settings.settings
  const b = k => s[k] === 'true' || s[k] === true
  form.value = {
    dry_run: b('dry_run'), log_level: s.log_level || 'INFO',
    max_parallel_library_scans: Number(s.max_parallel_library_scans || 3),
    scan_interval_minutes: Number(s.scan_interval_minutes || 360),
    deletion_check_interval_minutes: Number(s.deletion_check_interval_minutes || 60),
    deleted_retention_days: Number(s.deleted_retention_days || 30),
    log_retention_days: Number(s.log_retention_days || 30),
    job_history_retention_days: Number(s.job_history_retention_days || 30),
    backup_enabled: b('backup_enabled'), backup_interval_hours: Number(s.backup_interval_hours || 24),
    backup_retention_count: Number(s.backup_retention_count || 5), backup_path: s.backup_path || '',
    public_dashboard_enabled: b('public_dashboard_enabled'),
    emby_leaving_soon_overlay: b('emby_leaving_soon_overlay'),
    emby_leaving_soon_collection: s.emby_leaving_soon_collection || '',
    emby_leaving_soon_days: Number(s.emby_leaving_soon_days || 30),
    plex_tv_token: s.plex_tv_token || '', plex_webhook_secret: s.plex_webhook_secret || '',
    plex_overlay_enabled: b('plex_overlay_enabled'),
    radarr_url: s.radarr_url || '', radarr_api_key: s.radarr_api_key || '',
    sonarr_url: s.sonarr_url || '', sonarr_api_key: s.sonarr_api_key || '',
    seerr_url: s.seerr_url || '', seerr_api_key: s.seerr_api_key || '', seerr_external_url: s.seerr_external_url || '',
    qbit_url: s.qbit_url || '', qbit_proxy_url: s.qbit_proxy_url || '',
    qbit_user: s.qbit_user || '', qbit_password: s.qbit_password || '',
    qbit_action: s.qbit_action || '', qbit_tag: s.qbit_tag || '',
    discord_webhook: s.discord_webhook || '', discord_webhook_alerts: s.discord_webhook_alerts || '',
    discord_notif_thresholds: s.discord_notif_thresholds || '30,14,7,3,1',
    discord_alert_deletion_error: s.discord_alert_deletion_error || 'false',
    discord_alert_deletion_error_mention: s.discord_alert_deletion_error_mention || '',
    discord_alert_deletion_error_msg: s.discord_alert_deletion_error_msg || '',
    discord_alert_scan_failure: s.discord_alert_scan_failure || 'false',
    discord_alert_scan_failure_mention: s.discord_alert_scan_failure_mention || '',
    discord_alert_scan_failure_msg: s.discord_alert_scan_failure_msg || '',
    discord_alert_seerr_failure: s.discord_alert_seerr_failure || 'false',
    discord_alert_seerr_failure_mention: s.discord_alert_seerr_failure_mention || '',
    discord_alert_seerr_failure_msg: s.discord_alert_seerr_failure_msg || '',
    discord_alert_error_threshold: Number(s.discord_alert_error_threshold || 0),
  }
}

watch(() => settings.settings, () => { if (!saving.value) syncForm() }, { deep: true })

async function save() {
  saving.value = true; saved.value = false; saveError.value = ''
  try {
    const f = form.value
    await settings.save({
      dry_run: String(f.dry_run), log_level: f.log_level,
      max_parallel_library_scans: String(f.max_parallel_library_scans),
      scan_interval_minutes: String(f.scan_interval_minutes),
      deletion_check_interval_minutes: String(f.deletion_check_interval_minutes),
      deleted_retention_days: String(f.deleted_retention_days),
      log_retention_days: String(f.log_retention_days),
      job_history_retention_days: String(f.job_history_retention_days),
      backup_enabled: String(f.backup_enabled), backup_interval_hours: String(f.backup_interval_hours),
      backup_retention_count: String(f.backup_retention_count), backup_path: f.backup_path,
      public_dashboard_enabled: String(f.public_dashboard_enabled),
      emby_leaving_soon_overlay: String(f.emby_leaving_soon_overlay),
      emby_leaving_soon_collection: f.emby_leaving_soon_collection,
      emby_leaving_soon_days: String(f.emby_leaving_soon_days),
      plex_tv_token: f.plex_tv_token, plex_webhook_secret: f.plex_webhook_secret,
      plex_overlay_enabled: String(f.plex_overlay_enabled),
      radarr_url: f.radarr_url, radarr_api_key: f.radarr_api_key,
      sonarr_url: f.sonarr_url, sonarr_api_key: f.sonarr_api_key,
      seerr_url: f.seerr_url, seerr_api_key: f.seerr_api_key, seerr_external_url: f.seerr_external_url,
      qbit_url: f.qbit_url, qbit_proxy_url: f.qbit_proxy_url,
      qbit_user: f.qbit_user, qbit_password: f.qbit_password,
      qbit_action: f.qbit_action, qbit_tag: f.qbit_tag,
      discord_webhook: f.discord_webhook, discord_webhook_alerts: f.discord_webhook_alerts,
      discord_notif_thresholds: f.discord_notif_thresholds,
      discord_alert_deletion_error: f.discord_alert_deletion_error,
      discord_alert_deletion_error_mention: f.discord_alert_deletion_error_mention,
      discord_alert_deletion_error_msg: f.discord_alert_deletion_error_msg,
      discord_alert_scan_failure: f.discord_alert_scan_failure,
      discord_alert_scan_failure_mention: f.discord_alert_scan_failure_mention,
      discord_alert_scan_failure_msg: f.discord_alert_scan_failure_msg,
      discord_alert_seerr_failure: f.discord_alert_seerr_failure,
      discord_alert_seerr_failure_mention: f.discord_alert_seerr_failure_mention,
      discord_alert_seerr_failure_msg: f.discord_alert_seerr_failure_msg,
      discord_alert_error_threshold: String(f.discord_alert_error_threshold),
    })
    saved.value = true
    setTimeout(() => { saved.value = false }, 3000)
  } catch (e) {
    const msg = e?.response?.data?.detail || e?.message || 'Erreur inconnue'
    saveError.value = `Échec de la sauvegarde : ${msg}`
    setTimeout(() => { saveError.value = '' }, 6000)
  } finally { saving.value = false }
}

onMounted(async () => {
  await settings.fetch()
  syncForm()
})
</script>
```

- [ ] **Step 2: Verify build passes (no errors)**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | tail -5
```
Expected: `✓ built in Xs`

- [ ] **Step 3: Deploy and verify visually**

```bash
docker cp /opt/claude/hygie/frontend/dist/. hygie:/app/frontend/dist/ && docker restart hygie
```
Open Settings — all 7 tabs should render and function identically to before.

- [ ] **Step 4: Commit**

```bash
git -C /opt/claude/hygie add frontend/vue/src/views/SettingsView.vue
git -C /opt/claude/hygie commit -m "refactor(frontend): SettingsView.vue → thin shell, all tabs extracted"
```

---

### Task 10: Slim QueueView.vue — use extracted components

**Files:**
- Modify: `frontend/vue/src/views/QueueView.vue`

Replace the three `<Teleport>` modals with `<ConfirmModal>` and the inline `SortHeader defineComponent` with the extracted `<SortHeader>`.

- [ ] **Step 1: Replace QueueView.vue**

Full replacement — same logic, three modals become `<ConfirmModal>` instances, `SortHeader` defineComponent is removed:

```vue
<!-- frontend/vue/src/views/QueueView.vue -->
<template>
  <div class="space-y-4">
    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-2">
      <div class="flex bg-[var(--bg2)] border border-[var(--border)] rounded-lg p-0.5">
        <button
          v-for="f in STATUS_FILTERS"
          :key="f.value"
          @click="setFilter(f.value)"
          class="px-3 py-1.5 text-xs rounded transition-colors"
          :class="statusFilter === f.value ? 'bg-[var(--accent)] text-white' : 'text-[var(--muted)] hover:text-white'"
        >{{ f.label }}</button>
      </div>
      <input
        v-model="search"
        placeholder="Rechercher…"
        class="bg-[var(--bg2)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm w-48 focus:outline-none focus:border-[var(--accent)]"
      />
      <div class="ml-auto flex items-center gap-2">
        <span class="text-sm text-[var(--muted)]">{{ total }} élément(s)</span>
        <button @click="gridView = false" :class="['w-8 h-8 rounded border flex items-center justify-center transition-colors', !gridView ? 'bg-[var(--accent)] border-[var(--accent)]' : 'bg-[var(--bg2)] border-[var(--border)] text-[var(--muted)] hover:text-white']">
          <i class="fas fa-list text-xs" />
        </button>
        <button @click="gridView = true" :class="['w-8 h-8 rounded border flex items-center justify-center transition-colors', gridView ? 'bg-[var(--accent)] border-[var(--accent)]' : 'bg-[var(--bg2)] border-[var(--border)] text-[var(--muted)] hover:text-white']">
          <i class="fas fa-th text-xs" />
        </button>
        <button @click="confirmPurge = true" class="flex items-center gap-1.5 px-3 py-1.5 bg-[var(--bg2)] border border-[var(--border)] hover:border-red-500/50 hover:text-red-400 rounded-lg text-xs text-[var(--muted)] transition-colors">
          <i class="fas fa-trash-alt text-xs" /> Purger supprimés
        </button>
      </div>
    </div>

    <div v-if="error" class="bg-red-500/10 border border-red-500/30 text-red-400 rounded-lg px-4 py-3 text-sm">{{ error }}</div>
    <div v-if="loading" class="text-center py-12 text-[var(--muted)]"><i class="fas fa-spinner fa-spin text-2xl" /></div>

    <template v-else>
      <!-- LIST VIEW -->
      <div v-if="!gridView" class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-xs text-[var(--muted)] border-b border-[var(--border)]">
              <th class="w-14 px-3 py-2" />
              <th class="text-left px-4 py-2"><SortHeader label="Titre" field="title" :sort="sort" :dir="dir" @sort="setSort" /></th>
              <th class="text-left px-4 py-2 hidden md:table-cell"><SortHeader label="Bibliothèque" field="library_name" :sort="sort" :dir="dir" @sort="setSort" /></th>
              <th class="text-left px-4 py-2 hidden lg:table-cell"><SortHeader label="Demandeur" field="seerr_username" :sort="sort" :dir="dir" @sort="setSort" /></th>
              <th class="text-left px-4 py-2 hidden xl:table-cell"><SortHeader label="Vu le" field="last_played" :sort="sort" :dir="dir" @sort="setSort" /></th>
              <th class="text-left px-4 py-2"><SortHeader label="Statut" field="status" :sort="sort" :dir="dir" @sort="setSort" /></th>
              <th class="text-left px-4 py-2"><SortHeader label="Suppression" field="delete_at" :sort="sort" :dir="dir" @sort="setSort" /></th>
              <th class="w-24 px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in items"
              :key="item.id"
              class="border-b border-[var(--border)] hover:bg-[var(--bg3)] transition-colors"
              :class="rowUrgencyClass(item.delete_at, item.status)"
            >
              <td class="px-3 py-1.5 w-14">
                <div class="relative w-10 h-14 rounded overflow-hidden bg-[var(--bg3)] flex items-center justify-center">
                  <img v-if="item.poster_url" :src="`/api/proxy/image?url=${encodeURIComponent(item.poster_url)}`" :alt="item.title" class="w-full h-full object-cover absolute inset-0" loading="lazy" @error="e => e.target.style.display = 'none'" />
                  <i :class="['fas', isSeries(item.media_type) ? 'fa-tv' : 'fa-film', 'text-sm text-[var(--muted)] opacity-50']" />
                </div>
              </td>
              <td class="px-4 py-2 max-w-[180px] xl:max-w-xs">
                <a v-if="item.seerr_request_url" :href="item.seerr_request_url" target="_blank" class="font-medium truncate block hover:text-[var(--accent)] transition-colors" :title="item.title">{{ item.title }}</a>
                <span v-else class="font-medium truncate block" :title="item.title">{{ item.title }}</span>
              </td>
              <td class="px-4 py-2 text-[var(--muted)] hidden md:table-cell truncate max-w-[120px]">{{ item.library_name || '—' }}</td>
              <td class="px-4 py-2 hidden lg:table-cell">
                <a v-if="item.seerr_user_id && seerrExternalUrl" :href="`${seerrExternalUrl}/users/${item.seerr_user_id}`" target="_blank" class="text-[var(--muted)] hover:text-[var(--accent)] transition-colors">{{ item.seerr_username || '—' }}</a>
                <span v-else class="text-[var(--muted)]">{{ item.seerr_username || '—' }}</span>
              </td>
              <td class="px-4 py-2 text-xs hidden xl:table-cell whitespace-nowrap" :class="item.last_played ? 'text-[var(--muted)]' : 'text-red-400 font-medium'">
                {{ item.last_played ? formatDate(item.last_played) : 'Jamais vu' }}
              </td>
              <td class="px-4 py-2">
                <span class="px-2 py-0.5 rounded text-xs whitespace-nowrap" :class="statusClass(item.status)">{{ statusLabel(item.status) }}</span>
              </td>
              <td class="px-4 py-2 whitespace-nowrap">
                <span :class="daysClass(item.delete_at, item.status)" class="text-xs font-semibold block">{{ daysLabel(item.delete_at, item.status) }}</span>
                <span class="text-xs text-[var(--muted)]">{{ formatDate(item.delete_at) }}</span>
              </td>
              <td class="px-3 py-2">
                <div class="flex items-center gap-1.5" v-if="item.status === 'pending'">
                  <button @click="triggerDelete(item)" title="Supprimer maintenant" class="w-7 h-7 rounded flex items-center justify-center text-[var(--muted)] hover:text-red-400 hover:bg-red-500/10 transition-colors"><i class="fas fa-trash text-xs" /></button>
                  <button @click="openIgnore(item)" title="Ignorer" class="w-7 h-7 rounded flex items-center justify-center text-[var(--muted)] hover:text-yellow-400 hover:bg-yellow-500/10 transition-colors"><i class="fas fa-ban text-xs" /></button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- GRID VIEW -->
      <div v-else class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-3">
        <div
          v-for="item in items"
          :key="item.id"
          class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden group relative"
          :class="item.status !== 'pending' ? 'opacity-60' : ''"
        >
          <div class="relative aspect-[2/3] bg-[var(--bg3)] flex items-center justify-center overflow-hidden">
            <img v-if="item.poster_url" :src="`/api/proxy/image?url=${encodeURIComponent(item.poster_url)}`" :alt="item.title" class="w-full h-full object-cover" loading="lazy" @error="e => e.target.style.display = 'none'" />
            <i :class="['fas', isSeries(item.media_type) ? 'fa-tv' : 'fa-film', 'text-3xl text-[var(--muted)] opacity-30']" />
            <div class="absolute bottom-0 inset-x-0 py-1.5 px-2 text-center text-xs font-bold text-white" :class="gridBannerClass(item.delete_at, item.status)">{{ daysLabel(item.delete_at, item.status) }}</div>
            <div v-if="item.status === 'pending'" class="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-3 pb-8">
              <button @click.stop="triggerDelete(item)" title="Supprimer" class="w-9 h-9 rounded-full bg-red-500 hover:bg-red-600 flex items-center justify-center transition-colors"><i class="fas fa-trash text-sm text-white" /></button>
              <button @click.stop="openIgnore(item)" title="Ignorer" class="w-9 h-9 rounded-full bg-yellow-500 hover:bg-yellow-600 flex items-center justify-center transition-colors"><i class="fas fa-ban text-sm text-white" /></button>
            </div>
          </div>
          <div class="p-2">
            <a v-if="item.seerr_request_url" :href="item.seerr_request_url" target="_blank" class="text-xs font-medium truncate block hover:text-[var(--accent)] transition-colors" :title="item.title">{{ item.title }}</a>
            <span v-else class="text-xs font-medium truncate block" :title="item.title">{{ item.title }}</span>
            <div class="text-[10px] text-[var(--muted)] truncate">{{ item.library_name }}</div>
          </div>
        </div>
      </div>
    </template>

    <!-- Pagination -->
    <div v-if="totalPages > 1" class="flex items-center justify-center gap-1">
      <button @click="page = 1" :disabled="page === 1" class="w-8 h-8 rounded text-sm text-[var(--muted)] hover:bg-[var(--bg3)] disabled:opacity-30">«</button>
      <button v-for="p in visiblePages" :key="p" @click="typeof p === 'number' && (page = p)" class="w-8 h-8 rounded text-sm transition-colors" :class="p === page ? 'bg-[var(--accent)] text-white' : p === '…' ? 'cursor-default text-[var(--muted)]' : 'text-[var(--muted)] hover:bg-[var(--bg3)]'">{{ p }}</button>
      <button @click="page = totalPages" :disabled="page === totalPages" class="w-8 h-8 rounded text-sm text-[var(--muted)] hover:bg-[var(--bg3)] disabled:opacity-30">»</button>
    </div>

    <!-- Ignore modal -->
    <ConfirmModal :show="!!ignoreTarget" confirm-label="Ignorer" confirm-class="bg-yellow-500 hover:bg-yellow-600 text-white" @confirm="doIgnore" @cancel="ignoreTarget = null">
      <h3 class="font-semibold">Ignorer — {{ ignoreTarget?.title }}</h3>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Raison (optionnel)</label>
        <input v-model="ignoreReason" type="text" placeholder="Raison…" class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]" />
      </div>
      <div>
        <label class="block text-xs text-[var(--muted)] mb-1">Expiration (jours, 0 = permanent)</label>
        <input v-model.number="ignoreDays" type="number" min="0" class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]" />
      </div>
    </ConfirmModal>

    <!-- Delete confirm modal -->
    <ConfirmModal :show="!!deleteTarget" @confirm="doDelete" @cancel="deleteTarget = null">
      <p class="text-sm">Supprimer <strong>{{ deleteTarget?.title }}</strong> du serveur maintenant ?</p>
    </ConfirmModal>

    <!-- Purge confirm modal -->
    <ConfirmModal :show="confirmPurge" @confirm="doPurge" @cancel="confirmPurge = false">
      <p class="text-sm">Purger toutes les entrées avec statut "Supprimé" de la file d'attente ?</p>
    </ConfirmModal>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import api from '@/api/client'
import { useSettingsStore } from '@/stores/settings'
import SortHeader  from '@/components/ui/SortHeader.vue'
import ConfirmModal from '@/components/ui/ConfirmModal.vue'

const settings = useSettingsStore()

const STATUS_FILTERS = [
  { value: '',        label: 'Tous' },
  { value: 'pending', label: 'En attente' },
  { value: 'deleted', label: 'Supprimés' },
  { value: 'error',   label: 'Erreurs' },
]

const items        = ref([])
const total        = ref(0)
const page         = ref(1)
const statusFilter = ref('')
const search       = ref('')
const sort         = ref('delete_at')
const dir          = ref('asc')
const loading      = ref(false)
const error        = ref('')
const gridView     = ref(false)
const perPage      = 50

const ignoreTarget = ref(null)
const ignoreReason = ref('')
const ignoreDays   = ref(0)
const deleteTarget = ref(null)
const confirmPurge = ref(false)

const seerrExternalUrl = computed(() => {
  const s = settings.settings.seerr_external_url || ''
  return s.replace(/\/$/, '')
})

const totalPages = computed(() => Math.ceil(total.value / perPage))

const visiblePages = computed(() => {
  const n = totalPages.value, p = page.value
  if (n <= 7) return Array.from({ length: n }, (_, i) => i + 1)
  const start = Math.max(1, p - 2), end = Math.min(n, p + 2)
  const pages = []
  if (start > 2)        pages.push(1, '…')
  else if (start === 2) pages.push(1)
  for (let i = start; i <= end; i++) pages.push(i)
  if (end < n - 1)      pages.push('…', n)
  else if (end === n - 1) pages.push(n)
  return pages
})

function setSort(field) {
  if (sort.value === field) dir.value = dir.value === 'asc' ? 'desc' : 'asc'
  else { sort.value = field; dir.value = 'asc' }
  page.value = 1
  load()
}
function setFilter(v) { statusFilter.value = v; page.value = 1 }

const STATUS_LABELS  = { pending: 'En attente', deleted: 'Supprimé', error: 'Erreur' }
const STATUS_CLASSES = { pending: 'bg-yellow-500/20 text-yellow-400', deleted: 'bg-green-500/20 text-green-400', error: 'bg-red-700/20 text-red-300' }
function statusLabel(s) { return STATUS_LABELS[s] || s }
function statusClass(s) { return STATUS_CLASSES[s] || 'bg-[var(--bg3)] text-[var(--muted)]' }
function isSeries(t) { return t === 'Episode' || t === 'Series' || t === 'Season' }

function daysRemaining(deleteAt) {
  if (!deleteAt) return null
  return Math.ceil((new Date(deleteAt) - new Date()) / (1000 * 60 * 60 * 24))
}
function daysLabel(deleteAt, status) {
  if (status === 'deleted') return 'Supprimé'
  if (!deleteAt) return '—'
  const d = daysRemaining(deleteAt)
  if (d === null) return '—'
  if (d < 0)  return 'Dépassé'
  if (d === 0) return "Aujourd'hui"
  if (d === 1) return 'Demain'
  return `Dans ${d}j`
}
function daysClass(deleteAt, status) {
  if (status === 'deleted') return 'text-[var(--muted)]'
  const d = daysRemaining(deleteAt)
  if (d === null) return 'text-[var(--muted)]'
  if (d <= 3)  return 'text-red-400'
  if (d <= 7)  return 'text-orange-400'
  if (d <= 14) return 'text-yellow-400'
  return 'text-[var(--muted)]'
}
function gridBannerClass(deleteAt, status) {
  if (status === 'deleted') return 'bg-green-600/90'
  if (status === 'error')   return 'bg-red-700/90'
  const d = daysRemaining(deleteAt)
  if (d === null) return 'bg-[var(--bg3)]/80'
  if (d <= 3)  return 'bg-red-600/90'
  if (d <= 7)  return 'bg-orange-600/90'
  if (d <= 14) return 'bg-yellow-600/90'
  return 'bg-black/50'
}
function rowUrgencyClass(deleteAt, status) {
  if (status !== 'pending') return ''
  const d = daysRemaining(deleteAt)
  if (d === null) return ''
  if (d <= 3) return 'bg-red-500/5'
  if (d <= 7) return 'bg-orange-500/5'
  return ''
}
function formatDate(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric' })
}

async function load() {
  loading.value = true; error.value = ''
  try {
    const params = { limit: perPage, offset: (page.value - 1) * perPage, sort: sort.value, dir: dir.value }
    if (statusFilter.value) params.status = statusFilter.value
    if (search.value) params.search = search.value
    const { data } = await api.get('/media', { params })
    items.value = data.items || data || []
    total.value = data.total  || items.value.length
  } catch { error.value = "Impossible de charger la file d'attente." }
  finally { loading.value = false }
}

function openIgnore(item) { ignoreTarget.value = item; ignoreReason.value = ''; ignoreDays.value = 0 }
async function doIgnore() {
  if (!ignoreTarget.value) return
  await api.post(`/media/${ignoreTarget.value.id}/ignore`, null, {
    params: { reason: ignoreReason.value || undefined, expire_days: ignoreDays.value > 0 ? ignoreDays.value : undefined },
  })
  ignoreTarget.value = null
  load()
}

function triggerDelete(item) { deleteTarget.value = item }
async function doDelete() {
  if (!deleteTarget.value) return
  await api.post(`/media/${deleteTarget.value.id}/delete-now`)
  deleteTarget.value = null
  load()
}

async function doPurge() {
  confirmPurge.value = false
  await api.delete('/media/purge/deleted')
  load()
}

let searchTimer = null
watch(search, () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { page.value = 1; load() }, 300) })
watch([statusFilter, page], load)
onMounted(async () => { await settings.fetch(); load() })
</script>
```

- [ ] **Step 2: Verify build passes**

```bash
cd /opt/claude/hygie/frontend/vue && npm run build 2>&1 | tail -5
```
Expected: `✓ built in Xs`

- [ ] **Step 3: Deploy and do a full visual regression check**

```bash
docker cp /opt/claude/hygie/frontend/dist/. hygie:/app/frontend/dist/ && docker restart hygie
```

Check:
1. Queue list view + grid view load correctly
2. "Ignorer" modal opens with reason + days fields, and closes on cancel
3. Delete confirm modal works
4. Purge confirm modal works
5. Sort headers are clickable and toggle asc/desc
6. Pagination works

- [ ] **Step 4: Commit**

```bash
git -C /opt/claude/hygie add frontend/vue/src/views/QueueView.vue
git -C /opt/claude/hygie commit -m "refactor(frontend): QueueView.vue uses ConfirmModal + SortHeader components"
```

---

## Self-Review

**1. Spec coverage:**
- ✅ SettingsView.vue decomposed into 7 tab components + thin shell
- ✅ QueueView.vue inline defineComponents replaced by proper .vue files
- ✅ Three shared UI atoms extracted (TestBtn, ConfirmModal, SortHeader)
- ✅ No behavioral changes — same API calls, same form state serialization

**2. Placeholder scan:**
- No TBD or TODO in any task
- All components include complete template + script

**3. Type consistency:**
- `form` prop is consistently `{ type: Object, required: true }` across all tab components
- `TestBtn` `service` prop is consistently `{ type: String, required: true }`
- `ConfirmModal` emits `confirm` and `cancel` — used consistently in QueueView

**Noted edge case:** `DiscordTab.vue` uses `AlertRow` as an options-API local component (via `components:` and `template:` string). This works in Vue 3 but bypasses scoped styles for `.field`. The `<style scoped>` in DiscordTab.vue covers the outer template only. The `field` class in `AlertRow`'s inline template string will inherit the global `.field` rule from `index.css` if defined there, OR use the unscoped version. **Mitigation:** the `.field` style in this plan uses `@apply` — Tailwind will inline these at build time, so scoped vs unscoped doesn't matter for Tailwind classes.
