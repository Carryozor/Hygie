<!-- frontend/vue/src/views/RulesView.vue -->
<!-- Rule builder is implemented in Phase 4. This is a placeholder. -->
<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <h2 class="font-semibold">Règles</h2>
      <button
        class="bg-[var(--accent)] hover:opacity-90 rounded-lg px-4 py-2 text-sm font-medium transition-opacity"
        @click="showCreate = true"
      >
        + Nouvelle règle
      </button>
    </div>
    <div class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-8 text-center text-[var(--muted)] text-sm">
      Le gestionnaire de règles visuelles arrive en Phase 4.
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
const showCreate = ref(false)
</script>
