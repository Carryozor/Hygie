<template>
  <svg :width="size" :height="size" viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M 8 32 A 16 16 0 0 1 32 32" stroke="#6366f1" stroke-width="3" stroke-linecap="round" fill="none"/>
    <path d="M 11 32 A 11 11 0 0 1 29 32" stroke="#818cf8" stroke-width="2.5" stroke-linecap="round" fill="none"/>
    <path d="M 14 32 A 7 7 0 0 1 26 32" stroke="#a5b4fc" stroke-width="2" stroke-linecap="round" fill="none"/>
    <line x1="8" y1="14" x2="8" y2="32" stroke="#6366f1" stroke-width="3" stroke-linecap="round"/>
    <line x1="32" y1="14" x2="32" y2="32" stroke="#6366f1" stroke-width="3" stroke-linecap="round"/>
  </svg>
</template>
<script setup>
defineProps({ size: { type: Number, default: 40 } })
</script>
