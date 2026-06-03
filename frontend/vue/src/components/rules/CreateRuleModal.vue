<template>
  <!-- Backdrop -->
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      @mousedown.self="$emit('close')"
    >
      <div class="bg-[var(--bg1)] border border-[var(--border)] rounded-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto shadow-2xl">
        <!-- Header -->
        <div class="flex items-center justify-between px-6 py-4 border-b border-[var(--border)]">
          <h3 class="font-semibold text-base">
            {{ editRule ? t('rules.edit') : t('rules.new') }}
          </h3>
          <button type="button" class="text-[var(--muted)] hover:text-[var(--text)] transition-colors" @click="$emit('close')">
            <i class="fas fa-xmark" />
          </button>
        </div>

        <!-- Body -->
        <div class="p-6 space-y-6">
          <!-- Step 1: type selector (only for new rules) -->
          <template v-if="!editRule">
            <p class="text-xs text-[var(--muted)] uppercase tracking-wide">{{ t('rules.type') }}</p>
            <RuleTypeSelector v-model="ruleType" />
          </template>

          <!-- Step 2: form -->
          <template v-if="ruleType">
            <SimpleRuleForm
              v-if="ruleType === 'simple'"
              :initial="editRule || {}"
              @update:model-value="formData = $event"
            />
            <ExpertRuleBuilder
              v-else
              :initial="editRule || {}"
              @update:model-value="formData = $event"
            />
          </template>
        </div>

        <!-- Footer -->
        <div class="flex justify-end gap-3 px-6 py-4 border-t border-[var(--border)]">
          <button
            type="button"
            class="px-4 py-2 rounded-lg text-sm text-[var(--muted)] hover:text-[var(--text)] transition-colors"
            @click="$emit('close')"
          >
            {{ t('common.cancel') }}
          </button>
          <button
            type="button"
            :disabled="!canSave || saving"
            :class="[
              'px-5 py-2 rounded-lg text-sm font-medium transition-all',
              canSave && !saving
                ? 'bg-[var(--accent)] hover:opacity-90 text-white'
                : 'bg-[var(--border)] text-[var(--muted)] cursor-not-allowed',
            ]"
            @click="save"
          >
            <i v-if="saving" class="fas fa-spinner fa-spin mr-1" />
            {{ editRule ? t('common.save') : t('common.save') }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import RuleTypeSelector from './RuleTypeSelector.vue'
import SimpleRuleForm from './SimpleRuleForm.vue'
import ExpertRuleBuilder from './ExpertRuleBuilder.vue'

const { t } = useI18n()

const props = defineProps({
  open:     { type: Boolean, default: false },
  editRule: { type: Object, default: null },
  editType: { type: String, default: '' },
})
const emit = defineEmits(['close', 'saved'])

const ruleType = ref(props.editType || '')
const formData = ref({})
const saving   = ref(false)

watch(() => props.open, (v) => {
  if (v) {
    ruleType.value = props.editType || ''
    formData.value = {}
  }
})

watch(() => props.editRule, (r) => {
  if (r) formData.value = { ...r }
}, { immediate: true })

const canSave = computed(() => {
  if (!ruleType.value) return false
  if (ruleType.value === 'expert') {
    const hasGroups = formData.value.condition_groups?.some(g => g.conditions?.length > 0)
    return !!(formData.value.name && hasGroups)
  }
  return !!formData.value.seerr_user_id
})

async function save() {
  // We emit 'saved' synchronously, but the parent handler (onSaved) is async.
  // To properly track the async save state, we use a Promise that resolves
  // when the parent's handler finishes. The parent must call the resolve/reject
  // callbacks passed in the event payload (or we just keep saving=true until
  // the modal closes via props.open becoming false).
  saving.value = true
  try {
    // Emit the event. The parent's onSaved is async but emit() is sync,
    // so we can't directly await it here. We keep saving=true and let
    // the parent close the modal on success or keep it open on error.
    emit('saved', { type: ruleType.value, data: { ...formData.value } })
  } catch {
    saving.value = false
  }
}

// Reset saving state when the modal closes (parent sets open=false on success)
watch(() => props.open, (v) => {
  if (!v) saving.value = false
})
</script>
