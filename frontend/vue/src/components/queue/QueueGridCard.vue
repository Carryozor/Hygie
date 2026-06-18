<!-- frontend/vue/src/components/queue/QueueGridCard.vue -->
<template>
  <div
    class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden group relative"
    :class="[item.status !== 'pending' ? 'opacity-60' : '', serverDisabled ? 'opacity-40 border-orange-500/20' : '']"
    :title="serverDisabled ? 'Serveur désactivé — cet élément ne sera pas supprimé' : undefined"
  >
    <!-- Poster -->
    <div class="relative aspect-[2/3] bg-[var(--bg3)] flex items-center justify-center overflow-hidden">
      <img
        v-if="item.poster_url"
        :src="`/api/proxy/image?url=${encodeURIComponent(item.poster_url)}`"
        :alt="item.title"
        class="w-full h-full object-cover"
        loading="lazy"
        @error="e => e.target.style.display = 'none'"
      />
      <i :class="['fas', isSeries(item.media_type) ? 'fa-tv' : 'fa-film', 'text-3xl text-[var(--muted)] opacity-30']" />

      <!-- Days banner -->
      <div
        class="absolute bottom-0 inset-x-0 py-1.5 px-2 text-center text-xs font-bold text-white"
        :class="gridBannerClass(item.delete_at, item.status)"
      >
        {{ daysLabel(item.delete_at, item.status) }}
      </div>

      <!-- Actions overlay -->
      <div
        v-if="item.status === 'pending'"
        class="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-3 pb-8"
      >
        <button
          :title="t('common.delete')"
          class="w-9 h-9 rounded-full bg-red-500 hover:bg-red-600 flex items-center justify-center transition-colors"
          @click.stop="$emit('delete', item)"
        >
          <i class="fas fa-trash text-sm text-white" />
        </button>
        <button
          :title="t('queue.ignoreTitle')"
          class="w-9 h-9 rounded-full bg-yellow-500 hover:bg-yellow-600 flex items-center justify-center transition-colors"
          @click.stop="$emit('ignore', item)"
        >
          <i class="fas fa-ban text-sm text-white" />
        </button>
      </div>
    </div>

    <!-- Info -->
    <div class="p-2">
      <a
        v-if="item.seerr_request_url"
        :href="safeUrl(item.seerr_request_url)"
        target="_blank"
        class="text-xs font-medium truncate block hover:text-[var(--accent)] transition-colors"
        :title="item.title"
      >{{ item.title }}</a>
      <span v-else class="text-xs font-medium truncate block" :title="item.title">{{ item.title }}</span>
      <div class="text-[10px] text-[var(--muted)] truncate">{{ item.library_name }}</div>
      <div v-if="serverDisabled" class="text-[9px] text-orange-400 flex items-center gap-0.5 mt-0.5">
        <i class="fas fa-plug" />
        serveur off
      </div>
    </div>
  </div>
</template>

<script setup>
import { useI18n } from 'vue-i18n'
import { safeUrl } from '@/utils/safeUrl'

const { t } = useI18n()

defineProps({
  item:           { type: Object,  required: true },
  serverDisabled: { type: Boolean, default: false },
  daysLabel:      Function,
  gridBannerClass: Function,
  isSeries:       Function,
})

defineEmits(['delete', 'ignore'])
</script>
