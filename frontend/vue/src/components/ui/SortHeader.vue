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
