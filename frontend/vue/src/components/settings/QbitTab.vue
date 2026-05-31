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
        <input ref="tagInput" v-model="form.qbit_tag" type="text" placeholder="hygie-deleted" class="field" />
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, nextTick } from 'vue'
import ToggleSlider from '@/components/ui/ToggleSlider.vue'
import ServiceIcon from '@/components/ui/ServiceIcon.vue'
import TestBtn from '@/components/ui/TestBtn.vue'

const props   = defineProps({ form: { type: Object, required: true } })
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
