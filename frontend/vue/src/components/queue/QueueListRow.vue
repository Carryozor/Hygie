<!-- frontend/vue/src/components/queue/QueueListRow.vue -->
<template>
  <tr
    class="border-b border-[var(--border)] hover:bg-[var(--bg3)] transition-colors"
    :class="[rowUrgencyClass(item.delete_at, item.status), serverDisabled ? 'opacity-50' : '']"
    :title="serverDisabled ? 'Serveur désactivé — cet élément ne sera pas supprimé' : undefined"
  >
    <!-- Poster -->
    <td class="px-3 py-1.5 w-14">
      <div class="relative w-10 h-14 rounded overflow-hidden bg-[var(--bg3)] flex items-center justify-center">
        <img
          v-if="item.poster_url"
          :src="`/api/proxy/image?url=${encodeURIComponent(item.poster_url)}`"
          :alt="item.title"
          class="w-full h-full object-cover absolute inset-0"
          loading="lazy"
          @error="e => e.target.style.display = 'none'"
        />
        <i :class="['fas', isSeries(item.media_type) ? 'fa-tv' : 'fa-film', 'text-sm text-[var(--muted)] opacity-50']" />
      </div>
    </td>

    <!-- Title -->
    <td class="px-4 py-2 max-w-[180px] xl:max-w-xs">
      <a
        v-if="item.seerr_request_url"
        :href="item.seerr_request_url"
        target="_blank"
        class="font-medium truncate block hover:text-[var(--accent)] transition-colors"
        :title="item.title"
      >{{ item.title }}</a>
      <span v-else class="font-medium truncate block" :title="item.title">{{ item.title }}</span>
    </td>

    <!-- Server -->
    <td class="px-4 py-2 hidden md:table-cell">
      <span v-if="server" class="flex items-center gap-1.5">
        <span class="w-1.5 h-1.5 rounded-full flex-shrink-0"
          :class="{
            'bg-orange-400': server.type === 'plex',
            'bg-green-500':  server.type === 'emby',
            'bg-violet-500': server.type === 'jellyfin',
            'bg-[var(--muted)]': !server.type,
          }" />
        <span class="text-xs text-[var(--muted)] truncate max-w-[80px]">{{ server.name }}</span>
        <span v-if="serverDisabled" class="text-[9px] text-orange-400 bg-orange-400/10 rounded px-1">off</span>
      </span>
      <span v-else-if="serverDisabled" class="text-xs text-orange-400 flex items-center gap-1">
        <i class="fas fa-plug text-[9px]" />
        <span>désactivé</span>
      </span>
      <span v-else class="text-xs text-[var(--muted)]">—</span>
    </td>

    <!-- Library -->
    <td class="px-4 py-2 text-[var(--muted)] hidden md:table-cell truncate max-w-[120px] text-xs">
      {{ item.library_name || '—' }}
    </td>

    <!-- Requester -->
    <td class="px-4 py-2 hidden lg:table-cell">
      <a
        v-if="item.seerr_user_id && seerrExternalUrl"
        :href="`${seerrExternalUrl}/users/${item.seerr_user_id}`"
        target="_blank"
        class="text-[var(--muted)] hover:text-[var(--accent)] transition-colors"
      >{{ item.seerr_username || '—' }}</a>
      <span v-else class="text-[var(--muted)]">{{ item.seerr_username || '—' }}</span>
    </td>

    <!-- Last played -->
    <td
      class="px-4 py-2 text-xs hidden xl:table-cell whitespace-nowrap"
      :class="item.last_played ? 'text-[var(--muted)]' : (item.view_count > 0 ? 'text-yellow-500' : 'text-red-400 font-medium')"
    >
      {{ item.last_played
        ? formatDate(item.last_played)
        : item.view_count > 0
          ? t('queue.watchedUnknownDate') || '✓ Vu'
          : t('queue.neverWatched') }}
    </td>

    <!-- Added date -->
    <td class="px-4 py-2 text-xs hidden xl:table-cell whitespace-nowrap text-[var(--muted)]">
      {{ item.added_date ? formatDate(item.added_date) : '—' }}
    </td>

    <!-- Status -->
    <td class="px-4 py-2">
      <span class="px-2 py-0.5 rounded text-xs whitespace-nowrap" :class="statusClass(item.status)">
        {{ statusLabel(item.status) }}
      </span>
    </td>

    <!-- Deletion date -->
    <td class="px-4 py-2 whitespace-nowrap">
      <span :class="daysClass(item.delete_at, item.status)" class="text-xs font-semibold block">
        {{ daysLabel(item.delete_at, item.status) }}
      </span>
      <span class="text-xs text-[var(--muted)]">{{ formatDate(item.delete_at) }}</span>
    </td>

    <!-- Actions -->
    <td class="px-3 py-2">
      <div v-if="item.status === 'pending'" class="flex items-center gap-1.5">
        <button
          :title="t('queue.deleteNow')"
          class="w-7 h-7 rounded flex items-center justify-center text-[var(--muted)] hover:text-red-400 hover:bg-red-500/10 transition-colors"
          @click="$emit('delete', item)"
        ><i class="fas fa-trash text-xs" /></button>
        <button
          :title="t('queue.ignoreTitle')"
          class="w-7 h-7 rounded flex items-center justify-center text-[var(--muted)] hover:text-yellow-400 hover:bg-yellow-500/10 transition-colors"
          @click="$emit('ignore', item)"
        ><i class="fas fa-ban text-xs" /></button>
      </div>
    </td>
  </tr>
</template>

<script setup>
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const props = defineProps({
  item: { type: Object, required: true },
  server: { type: Object, default: null },
  seerrExternalUrl: { type: String, default: '' },
  serverDisabled: { type: Boolean, default: false },
  daysLabel: Function,
  daysClass: Function,
  rowUrgencyClass: Function,
  formatDate: Function,
  statusLabel: Function,
  statusClass: Function,
  isSeries: Function,
})

defineEmits(['delete', 'ignore'])
</script>
