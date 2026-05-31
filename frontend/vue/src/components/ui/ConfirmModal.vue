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
          >{{ cancelLabel || t('common.cancel') }}</button>
          <button
            class="px-4 py-2 text-sm rounded-lg transition-colors"
            :class="confirmClass"
            @click="$emit('confirm')"
          >{{ confirmLabel || t('common.confirm') }}</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

defineProps({
  show:         { type: Boolean, required: true },
  message:      { type: String,  default: '' },
  confirmLabel: { type: String,  default: '' },
  cancelLabel:  { type: String,  default: '' },
  confirmClass: { type: String,  default: 'bg-red-500 hover:bg-red-600 text-white' },
})
defineEmits(['confirm', 'cancel'])
</script>
