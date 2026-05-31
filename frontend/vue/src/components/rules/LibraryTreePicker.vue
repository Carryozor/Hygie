<template>
  <div class="space-y-1">
    <!-- All libraries option -->
    <label class="flex items-center gap-2.5 px-2.5 py-1.5 cursor-pointer hover:bg-[var(--bg3)] rounded-md transition-colors">
      <input
        type="radio"
        :checked="modelValue === null"
        class="accent-[var(--accent)]"
        @change="$emit('update:modelValue', null)"
      />
      <span class="text-sm font-medium">Toutes les bibliothèques</span>
    </label>

    <!-- Server nodes -->
    <div v-for="srv in serversWithLibraries" :key="srv.id" class="mt-1">
      <!-- Server header — checkbox zone + label zone + chevron zone sont séparés -->
      <div class="flex items-center gap-2 px-2.5 py-1.5 rounded-md hover:bg-[var(--bg3)] transition-colors select-none">
        <!-- Checkbox : sélectionne/désélectionne toutes les bibliothèques du serveur -->
        <div
          class="w-4 h-4 flex-shrink-0 rounded border flex items-center justify-center transition-colors cursor-pointer"
          :class="serverCheckState(srv) === 'all'
            ? 'bg-[var(--accent)] border-[var(--accent)]'
            : serverCheckState(srv) === 'partial'
              ? 'bg-[var(--accent)]/40 border-[var(--accent)]'
              : 'border-[var(--border)] bg-transparent'"
          @click.stop="toggleServer(srv)"
        >
          <i v-if="serverCheckState(srv) === 'all'" class="fas fa-check text-[8px] text-white" />
          <i v-else-if="serverCheckState(srv) === 'partial'" class="fas fa-minus text-[8px] text-white" />
        </div>
        <!-- Label : clic = expand/collapse uniquement -->
        <i :class="['fab', typeIcon(srv.type), 'text-xs text-[var(--muted)] w-3 text-center']" />
        <span class="text-sm font-medium flex-1 cursor-pointer" @click.stop="toggleExpand(srv)">{{ srv.name }}</span>
        <span class="text-[10px] text-[var(--muted)] cursor-pointer" @click.stop="toggleExpand(srv)">{{ srv.libs.length }} bib.</span>
        <!-- Chevron : expand/collapse uniquement -->
        <i
          class="fas fa-chevron-right text-[10px] text-[var(--muted)] transition-transform duration-150 cursor-pointer px-1"
          :class="expanded[srv.id] ? 'rotate-90' : ''"
          @click.stop="toggleExpand(srv)"
        />
      </div>

      <!-- Library children -->
      <div v-show="expanded[srv.id]" class="ml-6 mt-0.5 space-y-0.5">
        <label
          v-for="lib in srv.libs"
          :key="lib.id"
          class="flex items-center gap-2.5 px-2.5 py-1 cursor-pointer hover:bg-[var(--bg3)] rounded-md transition-colors"
          :class="isSelected(lib.id) ? 'bg-[var(--accent)]/5' : ''"
        >
          <input
            type="checkbox"
            :checked="isSelected(lib.id)"
            class="accent-[var(--accent)]"
            @change="toggleLib(lib.id)"
          />
          <span class="text-sm truncate">{{ lib.name }}</span>
        </label>
      </div>
    </div>

    <div v-if="!serversWithLibraries.length" class="text-xs text-[var(--muted)] text-center py-2">
      Aucune bibliothèque configurée
    </div>
  </div>
</template>

<script setup>
import { computed, reactive } from 'vue'
import { useServersStore } from '@/stores/servers'

const props = defineProps({
  modelValue: { type: Array, default: null }, // null = all; string[] = specific lib IDs
})
const emit = defineEmits(['update:modelValue'])

const serversStore = useServersStore()

const expanded = reactive({})

const serversWithLibraries = computed(() => {
  return serversStore.servers
    .filter(s => s.enabled !== false)
    .map(s => ({
      ...s,
      libs: serversStore.librariesForServer(s.id),
    }))
    .filter(s => s.libs.length > 0)
})

function typeIcon(type) {
  if (type === 'plex') return 'fa-plex'
  if (type === 'jellyfin') return 'fa-jellyfish'
  return 'fa-server'
}

function isSelected(libId) {
  if (!Array.isArray(props.modelValue)) return false
  return props.modelValue.includes(String(libId))
}

function serverCheckState(srv) {
  if (!Array.isArray(props.modelValue)) return 'none'
  const libIds = srv.libs.map(l => String(l.id))
  const selected = libIds.filter(id => props.modelValue.includes(id))
  if (selected.length === 0) return 'none'
  if (selected.length === libIds.length) return 'all'
  return 'partial'
}

function toggleExpand(srv) {
  expanded[srv.id] = !expanded[srv.id]
}

function toggleServer(srv) {
  const libIds = srv.libs.map(l => String(l.id))
  const state  = serverCheckState(srv)
  const current = Array.isArray(props.modelValue) ? [...props.modelValue] : []

  if (state === 'all') {
    const next = current.filter(id => !libIds.includes(id))
    emit('update:modelValue', next.length ? next : null)
  } else {
    const next = [...new Set([...current, ...libIds])]
    emit('update:modelValue', next)
  }
  // Ne pas auto-expand : le chevron gère l'expansion séparément
}

function toggleLib(libId) {
  const id = String(libId)
  const current = Array.isArray(props.modelValue) ? [...props.modelValue] : []
  const idx = current.indexOf(id)
  if (idx === -1) current.push(id)
  else current.splice(idx, 1)
  emit('update:modelValue', current.length ? current : null)
}
</script>
