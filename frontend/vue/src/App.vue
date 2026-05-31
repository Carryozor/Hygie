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
  <ToastNotification />
</template>

<script setup>
import { computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useStatusStore } from '@/stores/status'
import AppSidebar        from '@/components/layout/AppSidebar.vue'
import AppTopbar         from '@/components/layout/AppTopbar.vue'
import ToastNotification from '@/components/ui/ToastNotification.vue'

const route  = useRoute()
const router = useRouter()
const auth   = useAuthStore()
const status = useStatusStore()

const showLayout = computed(() => !route.meta.public)

function onUnauthorized() {
  auth.logout()
  router.push('/login')
}

onMounted(() => {
  window.addEventListener('hygie:unauthorized', onUnauthorized)
  if (auth.isLoggedIn) {
    auth.fetchMe()
    status.start()
  }
})

onUnmounted(() => {
  window.removeEventListener('hygie:unauthorized', onUnauthorized)
  status.stop()
})
</script>
