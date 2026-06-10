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
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore }     from '@/stores/auth'
import { useStatusStore }   from '@/stores/status'
import { useSettingsStore } from '@/stores/settings'
import AppSidebar        from '@/components/layout/AppSidebar.vue'
import AppTopbar         from '@/components/layout/AppTopbar.vue'
import ToastNotification from '@/components/ui/ToastNotification.vue'

const route    = useRoute()
const router   = useRouter()
const auth     = useAuthStore()
const status   = useStatusStore()
const settings = useSettingsStore()

const showLayout = computed(() => !route.meta.public)

function onUnauthorized() {
  auth.logout()
  // Do NOT redirect if already on a public route (public calendar, login, setup).
  // A stale token in localStorage would otherwise kick anonymous users off the
  // public calendar page when auth.fetchMe() returns 401.
  if (!route.meta.public) {
    router.push('/login')
  }
}

function startSession() {
  auth.fetchMe()
  // Load settings once globally — individual views read from this store
  // instead of each calling settings.fetch() independently.
  settings.fetch()
  status.start()
}

onMounted(() => {
  window.addEventListener('hygie:unauthorized', onUnauthorized)
  if (auth.isLoggedIn) {
    startSession()
  }
})

// Login/logout happen via SPA navigation — App.vue is never remounted, so
// onMounted alone would leave scheduler/health polling stopped until a full
// page reload (sidebar countdowns missing after a fresh login).
watch(() => auth.isLoggedIn, (loggedIn) => {
  if (loggedIn) startSession()
  else status.stop()
})

onUnmounted(() => {
  window.removeEventListener('hygie:unauthorized', onUnauthorized)
  status.stop()
})
</script>
