<template>
  <div>
  <div v-if="!items.length" class="py-8 text-center text-[var(--muted)] text-sm">
    Aucun élément.
  </div>
  <table v-else class="w-full text-sm">
    <thead>
      <tr class="text-xs text-[var(--muted)] border-b border-[var(--border)]">
        <th v-if="showServerDot" class="w-4 px-4 py-2" />
        <th class="text-left px-4 py-2">Titre</th>
        <th class="text-left px-4 py-2">Bibliothèque</th>
        <th class="text-left px-4 py-2">Statut</th>
        <th class="text-left px-4 py-2">Suppression le</th>
      </tr>
    </thead>
    <tbody>
      <tr
        v-for="item in items"
        :key="item.id"
        class="border-b border-[var(--border)] hover:bg-[var(--bg3)] transition-colors"
      >
        <td v-if="showServerDot" class="px-4 py-2">
          <span class="w-2 h-2 rounded-full inline-block bg-indigo-400" />
        </td>
        <td class="px-4 py-2 font-medium truncate max-w-[200px]">{{ item.title }}</td>
        <td class="px-4 py-2 text-[var(--muted)]">{{ item.library_name }}</td>
        <td class="px-4 py-2">
          <span class="px-2 py-0.5 rounded text-xs" :class="statusClass(item.status)">
            {{ item.status }}
          </span>
        </td>
        <td class="px-4 py-2 text-[var(--muted)]">{{ formatDate(item.delete_at) }}</td>
      </tr>
    </tbody>
  </table>
  </div>
</template>
<script setup>
defineProps({
  items:         { type: Array, default: () => [] },
  showServerDot: { type: Boolean, default: false },
})
function statusClass(status) {
  return {
    pending: 'bg-yellow-500/20 text-yellow-400',
    deleted: 'bg-red-500/20 text-red-400',
    error:   'bg-red-700/20 text-red-300',
  }[status] || 'bg-[var(--bg3)] text-[var(--muted)]'
}
function formatDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric' })
}
</script>
