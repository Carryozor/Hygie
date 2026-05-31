<template>
  <svg
    :width="total"
    :height="total"
    :viewBox="`0 0 ${total} ${total}`"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    class="flex-shrink-0"
  >
    <!-- ── 3 arc segments ─────────────────────────────────────────────── -->
    <!-- Arc 1 — top — indigo -->
    <circle
      :cx="C" :cy="C" :r="R"
      stroke="#6366f1"
      :stroke-opacity="arcOpacity"
      :stroke-width="arcW"
      stroke-linecap="round"
      :stroke-dasharray="`${arcLen} ${gapLen}`"
      :transform="`rotate(${arc1Start} ${C} ${C})`"
      :class="arcClass"
    />
    <!-- Arc 2 — bottom-right — green -->
    <circle
      :cx="C" :cy="C" :r="R"
      stroke="#22c55e"
      :stroke-opacity="arcOpacity"
      :stroke-width="arcW"
      stroke-linecap="round"
      :stroke-dasharray="`${arcLen} ${gapLen}`"
      :transform="`rotate(${arc1Start + 120} ${C} ${C})`"
      :class="arcClass"
    />
    <!-- Arc 3 — bottom-left — sky blue -->
    <circle
      :cx="C" :cy="C" :r="R"
      stroke="#38bdf8"
      :stroke-opacity="arcOpacity"
      :stroke-width="arcW"
      stroke-linecap="round"
      :stroke-dasharray="`${arcLen} ${gapLen}`"
      :transform="`rotate(${arc1Start + 240} ${C} ${C})`"
      :class="arcClass"
    />

    <!-- ── Hygie logo (centered via nested SVG) ───────────────────────── -->
    <svg
      :x="C - size / 2"
      :y="C - size / 2"
      :width="size"
      :height="size"
      viewBox="0 0 40 40"
      fill="none"
    >
      <line x1="9"  y1="8"  x2="9"  y2="32" stroke="#6366f1" stroke-width="3"   stroke-linecap="round"/>
      <line x1="31" y1="8"  x2="31" y2="32" stroke="#6366f1" stroke-width="3"   stroke-linecap="round"/>
      <path d="M 9 20 C 15 14 25 26 31 20"  stroke="#6366f1" stroke-width="2.5" stroke-linecap="round"/>
      <path d="M 9 32 A 14 7 0 0 0 31 32"   stroke="#6366f1" stroke-width="2.5" stroke-linecap="round"/>
      <path d="M 12 32 A 10 5 0 0 0 28 32"  stroke="#818cf8" stroke-width="2"   stroke-linecap="round"/>
      <path d="M 15 32 A 7 3.5 0 0 0 25 32" stroke="#a5b4fc" stroke-width="1.5" stroke-linecap="round"/>
    </svg>
  </svg>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  size:      { type: Number, default: 32 },
  statusDot: { type: String, default: 'none' },
  hasError:  { type: Boolean, default: false },
})

const status = computed(() => props.hasError ? 'error' : props.statusDot)

// ── Geometry ──────────────────────────────────────────────────────────────
const arcW      = 3
const pad       = 9
const total     = computed(() => props.size + pad * 2)
const C         = computed(() => total.value / 2)
const R         = computed(() => C.value - arcW / 2 - 1)
const circ      = computed(() => 2 * Math.PI * R.value)
const arcLen    = computed(() => (100 / 360) * circ.value)
const gapLen    = computed(() => circ.value - arcLen.value)
const arc1Start = -90 + 10   // start at top, slight offset

// ── Opacity: brillant OK, pâle sinon ─────────────────────────────────────
const arcOpacity = computed(() => {
  if (status.value === 'ok')    return 1
  if (status.value === 'error') return 1     // couleur propre mais clignotante
  return 0.28                                // pâle : non connecté / inconnu
})

// ── CSS animation class ───────────────────────────────────────────────────
const arcClass = computed(() => {
  if (status.value === 'ok')    return 'arc-ok'
  if (status.value === 'error') return 'arc-error'
  return ''
})
</script>

<style scoped>
/* OK : respiration lente avec glow */
@keyframes arc-breathe {
  0%, 100% { opacity: 1;   filter: drop-shadow(0 0 4px currentColor); }
  50%       { opacity: 0.5; filter: none; }
}
.arc-ok {
  animation: arc-breathe 2.8s ease-in-out infinite;
}

/* Erreur : clignotement dans la couleur propre de l'arc */
@keyframes arc-blink {
  0%, 49%   { opacity: 1; }
  50%, 100% { opacity: 0; }
}
.arc-error {
  animation: arc-blink 0.75s step-end infinite;
}
</style>
