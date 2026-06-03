<!-- frontend/vue/src/components/ui/ToastNotification.vue -->
<template>
  <Teleport to="body">
    <div class="fixed bottom-4 right-4 z-50 flex flex-col gap-2 pointer-events-none">
      <TransitionGroup name="toast">
        <div
          v-for="toast in toasts"
          :key="toast.id"
          class="pointer-events-auto flex items-start gap-3 px-4 py-3 rounded-xl shadow-lg text-sm max-w-sm border"
          :class="toastClass(toast.type)"
        >
          <i :class="['fas', toastIcon(toast.type), 'mt-0.5 flex-shrink-0']" />
          <span class="flex-1">{{ toast.message }}</span>
          <button
            class="flex-shrink-0 opacity-60 hover:opacity-100 transition-opacity"
            @click="remove(toast.id)"
          >
            <i class="fas fa-xmark text-xs" />
          </button>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'

const toasts = ref([])
let _counter = 0

function toastClass(type) {
  if (type === 'error')   return 'bg-red-500/15 border-red-500/30 text-red-300'
  if (type === 'warning') return 'bg-yellow-500/15 border-yellow-500/30 text-yellow-300'
  if (type === 'success') return 'bg-green-500/15 border-green-500/30 text-green-300'
  return 'bg-[var(--bg2)] border-[var(--border)] text-[var(--text)]'
}

function toastIcon(type) {
  if (type === 'error')   return 'fa-circle-exclamation'
  if (type === 'warning') return 'fa-triangle-exclamation'
  if (type === 'success') return 'fa-circle-check'
  return 'fa-circle-info'
}

function add(message, type = 'error') {
  const id = ++_counter
  toasts.value.push({ id, message, type })
  setTimeout(() => remove(id), 6000)
}

function remove(id) {
  toasts.value = toasts.value.filter(t => t.id !== id)
}

function onError(e) {
  add(e.detail.message, e.detail.type || 'error')
}

onMounted(()   => window.addEventListener('hygie:error', onError))
onUnmounted(() => window.removeEventListener('hygie:error', onError))
</script>

<style scoped>
.toast-enter-active,
.toast-leave-active { transition: all 0.3s ease; }
.toast-enter-from   { transform: translateX(100%); opacity: 0; }
.toast-leave-to     { transform: translateX(100%); opacity: 0; }
</style>
