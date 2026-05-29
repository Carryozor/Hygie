<template>
  <div class="min-h-screen flex bg-[var(--bg)]">
    <AppSidebar v-if="showLayout" />
    <div class="flex-1 flex flex-col min-w-0">
      <AppTopbar v-if="showLayout" />
      <main class="flex-1 overflow-auto p-6">
        <router-view />
      </main>
    </div>
  </div>
</template>
<script setup>
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import AppTopbar  from '@/components/layout/AppTopbar.vue'

const route = useRoute()
const auth  = useAuthStore()

const showLayout = computed(() => !route.meta.public)
onMounted(() => { if (auth.isLoggedIn) auth.fetchMe() })
</script>
