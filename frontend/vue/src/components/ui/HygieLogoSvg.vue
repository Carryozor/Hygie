<template>
  <svg
    :width="total"
    :height="total"
    :viewBox="`0 0 ${total} ${total}`"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    class="flex-shrink-0"
  >
    <!-- Dynamic arcs: 1 server = full circle, 2 = halves, 3 = thirds -->
    <circle
      v-for="(arc, i) in arcs"
      :key="i"
      :cx="C" :cy="C" :r="R"
      :stroke="arc.color"
      :stroke-opacity="arc.opacity"
      :stroke-width="arcW"
      stroke-linecap="round"
      :stroke-dasharray="`${arc.len} ${circ - arc.len}`"
      :transform="`rotate(${arc.startAngle} ${C} ${C})`"
      :class="arc.cls"
    />

    <!-- Hygie logo (centered) -->
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
  // serverResults = [{ok: bool, type: string}, ...] — one entry per configured server (max 3)
})

// ── Geometry ──────────────────────────────────────────────────────────────────
const arcW   = 3
const pad    = 9
const total  = computed(() => props.size + pad * 2)
const C      = computed(() => total.value / 2)
const R      = computed(() => C.value - arcW / 2 - 1)
const circ   = computed(() => 2 * Math.PI * R.value)

// ── Colors ────────────────────────────────────────────────────────────────────
const TYPE_COLOR = {
  plex:     '#E5A00D',   // Plex orange
  emby:     '#52B54B',   // Emby green
  jellyfin: '#AA5CC3',   // Jellyfin purple
}
const DIM   = '#7c3aed'
const RED   = '#ef4444'

function typeColor(type) {
  return TYPE_COLOR[(type || '').toLowerCase()] || '#6366f1'
}

// ── Arc computation ───────────────────────────────────────────────────────────
// Each configured server gets an equal slice of the circle.
// 1 server  → 1 arc of ~360° (full circle, tiny gap for round-cap)
// 2 servers → 2 arcs of ~180° each
// 3 servers → 3 arcs of ~120° each
// Gap between arcs: 6° when multiple servers, near-zero for a single server.

const arcs = computed(() => {
  const results = props.serverResults
  const isError = props.hasError

  // No servers configured → single dim arc
  if (!results.length) {
    const len = circ.value * 0.92  // slight gap for aesthetic
    return [{
      len,
      startAngle: -90,
      color: DIM,
      opacity: 0.3,
      cls: '',
    }]
  }

  const N        = results.length
  const GAP_DEG  = N === 1 ? 0 : 6                    // no gap for a single server
  const sliceDeg = 360 / N - GAP_DEG
  const sliceLen = (sliceDeg / 360) * circ.value       // plain value, we're inside computed()

  return results.map((srv, i) => {
    const startAngle = -90 + i * (360 / N)

    if (isError) {
      return { len: sliceLen, startAngle, color: RED, opacity: 1, cls: 'arc-error' }
    }
    if (srv.ok) {
      return { len: sliceLen, startAngle, color: typeColor(srv.type), opacity: 1, cls: '' }
    }
    return { len: sliceLen, startAngle, color: DIM, opacity: 0.35, cls: '' }
  })
})
</script>

<style scoped>
@keyframes arc-error {
  0%, 49%   { opacity: 1; }
  50%, 100% { opacity: 0; }
}
.arc-error {
  animation: arc-error 0.75s step-end infinite;
}
</style>
