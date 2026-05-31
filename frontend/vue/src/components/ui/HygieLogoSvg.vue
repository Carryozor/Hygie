<template>
  <svg
    :width="total"
    :height="total"
    :viewBox="`0 0 ${total} ${total}`"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    class="flex-shrink-0"
  >
    <!-- ── Arc 1 — top ─────────────────────────────────────────────────────── -->
    <circle
      :cx="C" :cy="C" :r="R"
      :stroke="arc1.color"
      :stroke-opacity="arc1.opacity"
      :stroke-width="arcW"
      stroke-linecap="round"
      :stroke-dasharray="`${arcLen} ${gapLen}`"
      :transform="`rotate(${arc1Start} ${C} ${C})`"
      :class="arc1.cls"
    />
    <!-- ── Arc 2 — bottom-right ──────────────────────────────────────────── -->
    <circle
      :cx="C" :cy="C" :r="R"
      :stroke="arc2.color"
      :stroke-opacity="arc2.opacity"
      :stroke-width="arcW"
      stroke-linecap="round"
      :stroke-dasharray="`${arcLen} ${gapLen}`"
      :transform="`rotate(${arc1Start + 120} ${C} ${C})`"
      :class="arc2.cls"
    />
    <!-- ── Arc 3 — bottom-left ───────────────────────────────────────────── -->
    <circle
      :cx="C" :cy="C" :r="R"
      :stroke="arc3.color"
      :stroke-opacity="arc3.opacity"
      :stroke-width="arcW"
      stroke-linecap="round"
      :stroke-dasharray="`${arcLen} ${gapLen}`"
      :transform="`rotate(${arc1Start + 240} ${C} ${C})`"
      :class="arc3.cls"
    />

    <!-- ── Hygie logo (centered via nested SVG) ───────────────────────────── -->
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
  size:          { type: Number,  default: 32 },
  statusDot:     { type: String,  default: 'none' },
  hasError:      { type: Boolean, default: false },
  serverResults: { type: Array,   default: () => [] },
  // serverResults = [{ok: bool}, ...] — one entry per enabled server (max 3)
})

const status = computed(() => props.hasError ? 'error' : props.statusDot)

// ── Geometry ──────────────────────────────────────────────────────────────────
const arcW      = 3
const pad       = 9
const total     = computed(() => props.size + pad * 2)
const C         = computed(() => total.value / 2)
const R         = computed(() => C.value - arcW / 2 - 1)
const circ      = computed(() => 2 * Math.PI * R.value)
const arcLen    = computed(() => (100 / 360) * circ.value)
const gapLen    = computed(() => circ.value - arcLen.value)
const arc1Start = -90 + 10

// ── Per-arc state ──────────────────────────────────────────────────────────────
// Each arc independently reflects its server's status.
// Vivid colors   : indigo #6366f1 (arc1), green #22c55e (arc2), sky #38bdf8 (arc3)
// Dim (offline)  : violet #7c3aed at 35% opacity
// Error          : red #ef4444 + blink

const VIVID  = ['#6366f1', '#22c55e', '#38bdf8']
const DIM_C  = '#7c3aed'
const RED    = '#ef4444'

function arcState(idx) {
  // Error state overrides everything
  if (status.value === 'error') {
    return { color: RED, opacity: 1, cls: 'arc-error' }
  }

  const results = props.serverResults
  if (!results.length) {
    // No data yet — dim violet until health check completes
    return { color: DIM_C, opacity: 0.35, cls: '' }
  }

  const srv = results[idx]
  if (!srv) {
    // No server at this position — very faint violet (slot unused)
    return { color: DIM_C, opacity: 0.15, cls: '' }
  }

  if (srv.ok) {
    // Server OK — vivid color, static
    return { color: VIVID[idx] || VIVID[0], opacity: 1, cls: '' }
  } else {
    // Server KO — dim violet
    return { color: DIM_C, opacity: 0.35, cls: '' }
  }
}

const arc1 = computed(() => arcState(0))
const arc2 = computed(() => arcState(1))
const arc3 = computed(() => arcState(2))
</script>

<style scoped>
/*
 * OK : respiration douce — opacity only, no filter (filter creates rectangular glow on SVG)
 * The glow is achieved by the opacity pulse against the dark background
 */
@keyframes arc-breathe {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.45; }
}
.arc-ok {
  animation: arc-breathe 2.8s ease-in-out infinite;
}

/* Error : clignotement rouge */
@keyframes arc-blink {
  0%, 49%   { opacity: 1; }
  50%, 100% { opacity: 0; }
}
.arc-error {
  animation: arc-blink 0.75s step-end infinite;
}
</style>
