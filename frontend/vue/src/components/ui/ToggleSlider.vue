<!-- frontend/vue/src/components/ui/ToggleSlider.vue -->
<template>
  <label class="relative inline-flex items-center cursor-pointer select-none" :class="{ 'opacity-50 cursor-not-allowed': disabled }">
    <input type="checkbox" class="sr-only peer" v-model="checked" :disabled="disabled" @change="$emit('update:modelValue', checked)" />
    <div class="w-10 h-5 rounded-full transition-colors peer-checked:bg-green-500 bg-red-500/70 peer-focus:ring-2 peer-focus:ring-[var(--accent)]" />
    <div class="absolute left-0.5 top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform peer-checked:translate-x-5" />
  </label>
</template>

<script setup>
import { ref, watch } from 'vue'
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  disabled:   { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])
const checked = ref(props.modelValue)
watch(() => props.modelValue, v => { checked.value = v })
</script>
