<!-- frontend/vue/src/views/PublicView.vue — no-auth public dashboard -->
<template>
  <div class="min-h-screen bg-[var(--bg1)] text-[var(--text)] p-4 md:p-8">
    <!-- Header -->
    <div class="flex items-center justify-between mb-4">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-full bg-[var(--accent)]/20 flex items-center justify-center">
          <i class="fas fa-film text-[var(--accent)]" />
        </div>
        <div>
          <h1 class="text-xl font-bold">Hygie</h1>
          <p class="text-xs text-[var(--muted)]">{{ lbl('title') }}</p>
        </div>
      </div>
      <!-- Language selector -->
      <div class="flex items-center gap-1">
        <button
          v-for="lang in SUPPORTED_LANGS" :key="lang"
          class="px-2 py-1 rounded text-xs font-medium transition-colors"
          :class="language === lang ? 'bg-[var(--accent)] text-white' : 'text-[var(--muted)] hover:text-white hover:bg-[var(--bg3)]'"
          @click="setLanguage(lang)"
        >{{ lang.toUpperCase() }}</button>
      </div>
    </div>
    <div class="text-xs text-[var(--muted)] mb-6">{{ totalUpcoming }} {{ lbl('planned') }}</div>

    <!-- Disabled message -->
    <div v-if="disabled" class="text-center py-20 text-[var(--muted)]">
      <i class="fas fa-lock text-4xl mb-4 block opacity-30" />
      <p class="text-sm">{{ lbl('disabled') }}</p>
    </div>

    <!-- Password form -->
    <div v-else-if="needPassword" class="flex items-center justify-center py-20">
      <div class="w-full max-w-sm bg-[var(--bg2)] border border-[var(--border)] rounded-2xl p-8 space-y-5">
        <div class="text-center">
          <i class="fas fa-key text-3xl text-[var(--accent)] mb-3 block" />
          <h2 class="text-lg font-semibold">{{ lbl('passwordRequired') }}</h2>
          <p class="text-xs text-[var(--muted)] mt-1">{{ lbl('passwordHint') }}</p>
        </div>
        <div class="space-y-3">
          <input
            v-model="passwordInput"
            type="password"
            :placeholder="lbl('passwordPlaceholder')"
            class="w-full bg-[var(--bg3)] border border-[var(--border)] rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-[var(--accent)]"
            :class="wrongPassword ? 'border-red-500/50' : ''"
            @keydown.enter="submitPassword"
            autofocus
          />
          <p v-if="wrongPassword" class="text-xs text-red-400">{{ lbl('wrongPassword') }}</p>
          <button
            class="w-full bg-[var(--accent)] hover:opacity-90 rounded-lg py-2.5 text-sm font-semibold transition-opacity"
            @click="submitPassword"
          >{{ lbl('submit') }}</button>
        </div>
      </div>
    </div>

    <template v-else-if="!loading">
      <!-- Server filter tabs -->
      <div v-if="servers.length > 1" class="flex flex-wrap gap-2 mb-6">
        <button
          class="px-3 py-1 rounded-full text-xs font-medium transition-colors"
          :class="selectedServerId === null ? 'bg-[var(--accent)] text-white' : 'bg-[var(--bg2)] border border-[var(--border)] text-[var(--muted)] hover:text-white'"
          @click="selectedServerId = null; selectedDay = null"
        >{{ lbl('all') }}</button>
        <button
          v-for="srv in servers" :key="srv.id"
          class="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium transition-colors"
          :class="selectedServerId === srv.id ? 'bg-[var(--accent)] text-white' : 'bg-[var(--bg2)] border border-[var(--border)] text-[var(--muted)] hover:text-white'"
          @click="selectedServerId = srv.id; selectedDay = null"
        >
          <span class="w-1.5 h-1.5 rounded-full" :class="typeColor(srv.type)" />
          {{ srv.name }}
        </button>
      </div>

      <!-- Calendar -->
      <div class="mb-8">
        <!-- Month nav -->
        <div class="flex items-center justify-between mb-4">
          <button class="w-8 h-8 rounded-lg bg-[var(--bg2)] border border-[var(--border)] hover:bg-[var(--bg3)] transition-colors flex items-center justify-center" @click="prevMonth">
            <i class="fas fa-chevron-left text-xs" />
          </button>
          <div class="flex items-center gap-2">
            <h2 class="font-semibold capitalize">{{ monthLabel }}</h2>
            <button
              v-if="!isCurrentMonth" class="text-xs px-2 py-0.5 rounded-full bg-[var(--accent)]/20 text-[var(--accent)] hover:bg-[var(--accent)]/30 transition-colors"
              @click="goToToday">
              {{ lbl('today') }}
            </button>
          </div>
          <button class="w-8 h-8 rounded-lg bg-[var(--bg2)] border border-[var(--border)] hover:bg-[var(--bg3)] transition-colors flex items-center justify-center" @click="nextMonth">
            <i class="fas fa-chevron-right text-xs" />
          </button>
        </div>

        <div class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden">
          <div class="grid grid-cols-7 border-b border-[var(--border)]">
            <div v-for="d in dayNames" :key="d" class="text-center text-xs font-semibold py-2 text-[var(--muted)] uppercase tracking-wide">{{ d }}</div>
          </div>
          <div>
            <div v-for="(week, wi) in grid" :key="wi" class="grid grid-cols-7 border-b border-[var(--border)] last:border-b-0">
              <div
                v-for="(cell, di) in week"
                :key="di"
                class="min-h-[70px] border-r border-[var(--border)] last:border-r-0 p-1"
                :class="cell && cell.events.length ? 'cursor-pointer hover:bg-[var(--bg3)]' : (!cell ? 'bg-[var(--bg3)]/30' : '')"
                @click="cell && cell.events.length && openDay(cell)"
              >
                <template v-if="cell">
                  <div
                    class="w-6 h-6 rounded-full flex items-center justify-center text-xs font-semibold mb-1"
                    :class="isToday(cell) ? 'bg-[var(--accent)] text-white' : isPast(cell) ? 'text-[var(--muted)]' : 'text-[var(--text)]'">
                    {{ cell.day }}
                  </div>
                  <div v-if="cell.events.length" class="space-y-0.5">
                    <div
                      v-for="item in cell.events.slice(0, 2)" :key="item.id"
                      class="truncate text-[10px] px-1 py-0.5 rounded bg-[var(--accent)]/20 text-[var(--accent)]"
                      :title="item.title">{{ item.title }}</div>
                    <div v-if="cell.events.length > 2" class="text-[10px] text-[var(--muted)] px-1">+{{ cell.events.length - 2 }} {{ lbl('more') }}</div>
                  </div>
                </template>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Selected day panel -->
      <transition name="slide-down">
        <div v-if="selectedDay" class="mt-4 bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden">
          <div class="flex items-center justify-between px-5 py-3 border-b border-[var(--border)]">
            <h3 class="font-semibold capitalize text-sm">{{ selectedDayLabel }}</h3>
            <button class="text-[var(--muted)] hover:text-white w-7 h-7 flex items-center justify-center rounded hover:bg-[var(--bg3)] transition-colors" @click="selectedDay = null">
              <i class="fas fa-xmark text-xs" />
            </button>
          </div>
          <div class="divide-y divide-[var(--border)]">
            <div
              v-for="item in selectedDay.events" :key="item.id"
              class="flex items-center gap-4 px-5 py-3 group"
              :class="mediaLink(item) ? 'cursor-pointer hover:bg-[var(--bg3)] transition-colors' : ''"
              @click="mediaLink(item) && openMedia(item)">
              <div class="relative w-9 h-12 rounded overflow-hidden bg-[var(--bg3)] flex-shrink-0 flex items-center justify-center">
                <img
                  v-if="item.poster_url"
                  :src="`/api/proxy/image?url=${encodeURIComponent(item.poster_url)}`"
                  :alt="item.title"
                  class="w-full h-full object-cover absolute inset-0"
                  loading="lazy"
                  @error="e => e.target.style.display = 'none'" />
                <i :class="['fas', item.media_type === 'Episode' || item.media_type === 'Series' ? 'fa-tv' : 'fa-film', 'text-xs text-[var(--muted)] opacity-50']" />
              </div>
              <div class="flex-1 min-w-0">
                <div class="font-medium text-sm truncate flex items-center gap-1.5">
                  {{ item.title }}
                  <i v-if="mediaLink(item)" class="fas fa-arrow-up-right-from-square text-[10px] text-[var(--muted)] opacity-0 group-hover:opacity-100 transition-opacity" />
                </div>
                <div class="flex items-center gap-1.5 text-xs text-[var(--muted)]">
                  <span v-if="serverName(item.server_id)" class="flex items-center gap-1">
                    <span class="w-1.5 h-1.5 rounded-full" :class="typeColor(serverType(item.server_id))" />
                    {{ serverName(item.server_id) }}
                    <span class="opacity-40">›</span>
                  </span>
                  {{ item.library_name }}
                </div>
              </div>
              <!-- View on server link -->
              <a
                v-if="serverItemUrl(item)"
                :href="safeUrl(serverItemUrl(item))"
                target="_blank"
                rel="noopener"
                class="flex-shrink-0 flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-[var(--border)] text-xs text-[var(--muted)] hover:text-white hover:border-[var(--accent)]/50 transition-colors"
                @click.stop
              >
                <i class="fas fa-arrow-up-right-from-square text-[10px]" />
                {{ lbl('viewOn') }} {{ serverName(item.server_id) }}
              </a>
            </div>
          </div>
        </div>
      </transition>

      <!-- Server / Library tree -->
      <div v-if="serverTree.length" class="mt-8">
        <h2 class="font-semibold text-sm mb-3 text-[var(--muted)] uppercase tracking-widest">{{ lbl('serversLibraries') }}</h2>
        <div class="space-y-3">
          <div v-for="srv in serverTree" :key="srv.id" class="bg-[var(--bg2)] border border-[var(--border)] rounded-xl overflow-hidden">
            <div class="flex items-center gap-2 px-4 py-2.5 border-b border-[var(--border)] bg-[var(--bg3)]/40">
              <span class="w-2 h-2 rounded-full" :class="typeColor(srv.type)" />
              <span class="font-semibold text-sm">{{ srv.name }}</span>
              <span class="ml-auto text-xs text-[var(--muted)]">{{ srv.totalEvents }} {{ lbl('planned') }}</span>
            </div>
            <div class="divide-y divide-[var(--border)]">
              <div v-for="lib in srv.libraries" :key="lib.id" class="flex items-center justify-between px-4 py-2">
                <div class="flex items-center gap-2">
                  <i class="fas fa-layer-group text-[var(--accent)] text-xs" />
                  <span class="text-sm">{{ lib.name }}</span>
                </div>
                <span class="text-xs text-[var(--muted)]">{{ lib.eventCount }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>

    <div v-else class="text-center py-20 text-[var(--muted)]">
      <i class="fas fa-spinner fa-spin text-2xl" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { safeUrl } from '@/utils/safeUrl'

const route = useRoute()

// ── Per-language labels (inline — no vue-i18n needed, public page) ─────────────
const LABELS = {
  fr: { title: 'Prochaines suppressions', today: "Aujourd'hui", planned: 'planifié(s)', more: 'autres', libraries: 'Bibliothèques', serversLibraries: 'Serveurs & Bibliothèques', loading: 'Chargement…', disabled: "Le tableau de bord public n'est pas activé.", passwordRequired: 'Accès protégé', passwordHint: 'Entrez le mot de passe pour accéder au calendrier.', passwordPlaceholder: 'Mot de passe…', wrongPassword: 'Mot de passe incorrect.', submit: 'Accéder', all: 'Tous', viewOn: 'Voir sur' },
  en: { title: 'Upcoming deletions', today: 'Today', planned: 'scheduled', more: 'more', libraries: 'Libraries', serversLibraries: 'Servers & Libraries', loading: 'Loading…', disabled: 'The public dashboard is not enabled.', passwordRequired: 'Protected access', passwordHint: 'Enter the password to access the calendar.', passwordPlaceholder: 'Password…', wrongPassword: 'Incorrect password.', submit: 'Access', all: 'All', viewOn: 'View on' },
  de: { title: 'Bevorstehende Löschungen', today: 'Heute', planned: 'geplant', more: 'weitere', libraries: 'Bibliotheken', serversLibraries: 'Server & Bibliotheken', loading: 'Laden…', disabled: 'Das öffentliche Dashboard ist nicht aktiviert.', passwordRequired: 'Geschützter Zugang', passwordHint: 'Geben Sie das Passwort ein, um auf den Kalender zuzugreifen.', passwordPlaceholder: 'Passwort…', wrongPassword: 'Falsches Passwort.', submit: 'Zugreifen', all: 'Alle', viewOn: 'Ansehen auf' },
  es: { title: 'Próximas eliminaciones', today: 'Hoy', planned: 'programado(s)', more: 'más', libraries: 'Bibliotecas', serversLibraries: 'Servidores y Bibliotecas', loading: 'Cargando…', disabled: 'El panel público no está habilitado.', passwordRequired: 'Acceso protegido', passwordHint: 'Ingrese la contraseña para acceder al calendario.', passwordPlaceholder: 'Contraseña…', wrongPassword: 'Contraseña incorrecta.', submit: 'Acceder', all: 'Todos', viewOn: 'Ver en' },
  it: { title: 'Eliminazioni prossime', today: 'Oggi', planned: 'pianificato/i', more: 'altri', libraries: 'Librerie', serversLibraries: 'Server e Librerie', loading: 'Caricamento…', disabled: 'La dashboard pubblica non è abilitata.', passwordRequired: 'Accesso protetto', passwordHint: "Inserisci la password per accedere al calendario.", passwordPlaceholder: 'Password…', wrongPassword: 'Password errata.', submit: 'Accedi', all: 'Tutti', viewOn: 'Vedi su' },
  pt: { title: 'Próximas eliminações', today: 'Hoje', planned: 'agendado(s)', more: 'mais', libraries: 'Bibliotecas', serversLibraries: 'Servidores e Bibliotecas', loading: 'Carregando…', disabled: 'O painel público não está habilitado.', passwordRequired: 'Acesso protegido', passwordHint: 'Insira a senha para acessar o calendário.', passwordPlaceholder: 'Senha…', wrongPassword: 'Senha incorreta.', submit: 'Acessar', all: 'Todos', viewOn: 'Ver em' },
  nl: { title: 'Aankomende verwijderingen', today: 'Vandaag', planned: 'gepland', more: 'meer', libraries: "Bibliotheken", serversLibraries: 'Servers & Bibliotheken', loading: 'Laden…', disabled: 'Het publieke dashboard is niet ingeschakeld.', passwordRequired: 'Beveiligde toegang', passwordHint: 'Voer het wachtwoord in om toegang te krijgen tot de kalender.', passwordPlaceholder: 'Wachtwoord…', wrongPassword: 'Onjuist wachtwoord.', submit: 'Toegang', all: 'Alle', viewOn: 'Bekijk op' },
  pl: { title: 'Nadchodzące usunięcia', today: 'Dzisiaj', planned: 'zaplanowane', more: 'więcej', libraries: 'Biblioteki', serversLibraries: 'Serwery i Biblioteki', loading: 'Ładowanie…', disabled: 'Publiczny panel nie jest włączony.', passwordRequired: 'Dostęp chroniony', passwordHint: 'Wprowadź hasło, aby uzyskać dostęp do kalendarza.', passwordPlaceholder: 'Hasło…', wrongPassword: 'Nieprawidłowe hasło.', submit: 'Dostęp', all: 'Wszystkie', viewOn: 'Zobacz na' },
}

const SUPPORTED_LANGS = ['fr', 'en', 'de', 'es', 'it', 'pt', 'nl', 'pl']
const LANG_KEY = 'hygie_public_lang'

// BCP-47 locale for Intl date formatting
const BCP47 = { fr: 'fr-FR', en: 'en-US', de: 'de-DE', es: 'es-ES', it: 'it-IT', pt: 'pt-PT', nl: 'nl-NL', pl: 'pl-PL' }

// Short day names ordered Mon–Sun per BCP-47 locale
function buildDayNames(lang) {
  const locale = BCP47[lang] || 'fr-FR'
  const base = new Date(2024, 0, 1) // Monday Jan 1 2024
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(base); d.setDate(1 + i)
    return new Intl.DateTimeFormat(locale, { weekday: 'short' }).format(d)
  })
}

const language   = ref('fr')
const events     = ref({})
const libraries  = ref([])
const servers    = ref([])
const loading    = ref(true)
const disabled   = ref(false)
const needPassword  = ref(false)
const wrongPassword = ref(false)
const passwordInput = ref('')
const selectedDay   = ref(null)
const selectedServerId = ref(null)

const slug = computed(() => route.params.slug || '')
const SESSION_KEY = 'hygie_public_pwd'
function storedPassword() { return sessionStorage.getItem(SESSION_KEY) || '' }

const labels  = computed(() => LABELS[language.value] || LABELS.fr)
function lbl(key) { return labels.value[key] ?? key }

const dayNames = computed(() => buildDayNames(language.value))
const intlLocale = computed(() => BCP47[language.value] || 'fr-FR')

const today     = new Date()
const viewYear  = ref(today.getFullYear())
const viewMonth = ref(today.getMonth())

const monthLabel = computed(() =>
  new Date(viewYear.value, viewMonth.value, 1)
    .toLocaleDateString(intlLocale.value, { month: 'long', year: 'numeric' })
)

const selectedDayLabel = computed(() => {
  if (!selectedDay.value) return ''
  return new Date(selectedDay.value.dateStr + 'T12:00:00')
    .toLocaleDateString(intlLocale.value, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
})

// ── Server helpers ─────────────────────────────────────────────────────────────
function setLanguage(lang) {
  language.value = lang
  localStorage.setItem(LANG_KEY, lang)
}

function serverById(sid) {
  return servers.value.find(s => String(s.id) === String(sid))
}
function serverName(sid) { return serverById(sid)?.name || '' }
function serverType(sid) { return serverById(sid)?.type || '' }

function serverItemUrl(item) {
  const srv = serverById(item.server_id)
  if (!srv) return null
  const base = (srv.ext_url || '').replace(/\/$/, '')
  if (!base) return null
  const embyId = item.emby_id
  if (!embyId) return base
  const uid = srv.server_uid || ''
  if (srv.type === 'emby')
    return `${base}/web/index.html#!/item?id=${embyId}${uid ? `&serverId=${uid}` : ''}`
  if (srv.type === 'jellyfin')
    return `${base}/web/index.html#!/details?id=${embyId}${uid ? `&serverId=${uid}` : ''}`
  if (srv.type === 'plex')
    // Plex web — rating key stored as emby_id. The #!/item?key= path works when
    // the user is already authenticated on the same Plex instance.
    return `${base}/web/index.html#!/item?key=/library/metadata/${embyId}`
  return base
}

function typeColor(type) {
  if (type === 'plex')     return 'bg-orange-400'
  if (type === 'emby')     return 'bg-green-500'
  if (type === 'jellyfin') return 'bg-violet-500'
  return 'bg-[var(--muted)]'
}

// ── Filtered events (by selected server) ───────────────────────────────────────
const filteredEvents = computed(() => {
  if (!selectedServerId.value) return events.value
  const out = {}
  for (const [date, items] of Object.entries(events.value)) {
    const filtered = items.filter(i => String(i.server_id) === String(selectedServerId.value))
    if (filtered.length) out[date] = filtered
  }
  return out
})

const totalUpcoming = computed(() =>
  Object.values(filteredEvents.value).reduce((s, arr) => s + arr.length, 0)
)

// ── Calendar grid ───────────────────────────────────────────────────────────────
const grid = computed(() => {
  const y = viewYear.value, m = viewMonth.value
  const firstDay = new Date(y, m, 1)
  const lastDate = new Date(y, m + 1, 0).getDate()
  let offset = firstDay.getDay()
  offset = offset === 0 ? 6 : offset - 1
  const weeks = []
  let day = 1
  for (let w = 0; w < 6; w++) {
    const row = []
    for (let d = 0; d < 7; d++) {
      const idx = w * 7 + d
      if (idx < offset || day > lastDate) { row.push(null) }
      else {
        const dateStr = `${y}-${String(m + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`
        row.push({ day, dateStr, events: filteredEvents.value[dateStr] || [] })
        day++
      }
    }
    weeks.push(row)
    if (day > lastDate) break
  }
  return weeks
})

const isCurrentMonth = computed(() =>
  viewYear.value === today.getFullYear() && viewMonth.value === today.getMonth()
)

function isToday(cell) {
  return cell.day === today.getDate() && viewMonth.value === today.getMonth() && viewYear.value === today.getFullYear()
}
function isPast(cell) { return new Date(cell.dateStr + 'T23:59:59') < today }
function openDay(cell) { selectedDay.value = cell }
function prevMonth() {
  selectedDay.value = null
  if (viewMonth.value === 0) { viewMonth.value = 11; viewYear.value-- } else viewMonth.value--
}
function nextMonth() {
  selectedDay.value = null
  if (viewMonth.value === 11) { viewMonth.value = 0; viewYear.value++ } else viewMonth.value++
}
function goToToday() {
  selectedDay.value = null
  viewYear.value  = today.getFullYear()
  viewMonth.value = today.getMonth()
}

function mediaLink(item) {
  if (item.tmdb_id) {
    const isTv = item.media_type === 'Series' || item.media_type === 'Episode'
    return `https://www.themoviedb.org/${isTv ? 'tv' : 'movie'}/${item.tmdb_id}`
  }
  return null
}
function openMedia(item) {
  const url = mediaLink(item)
  if (url) window.open(url, '_blank', 'noopener')
}

// ── Server/library tree with event counts ──────────────────────────────────────
const serverTree = computed(() => {
  const allEvents = Object.values(events.value).flat()
  return servers.value.map(srv => {
    const srvLibs = libraries.value.filter(l => String(l.server_id) === String(srv.id))
    const libsWithCount = srvLibs.map(lib => ({
      ...lib,
      eventCount: allEvents.filter(e => e.library_id === lib.id).length,
    })).filter(l => l.eventCount > 0)
    const totalEvents = allEvents.filter(e => String(e.server_id) === String(srv.id)).length
    return { ...srv, libraries: libsWithCount, totalEvents }
  }).filter(s => s.totalEvents > 0)
})

// ── Data loading ──────────────────────────────────────────────────────────────
async function loadDashboard(password = '') {
  loading.value = true
  const params = new URLSearchParams()
  if (slug.value) params.set('slug', slug.value)
  try {
    const headers = {}
    // Password travels in a header, not the query string — query params leak
    // into server access logs, browser history and the Referer header.
    if (password) headers['X-Dashboard-Password'] = password
    // Send admin token if present so admins bypass the public password requirement
    const token = localStorage.getItem('hygie_token')
    if (token) headers['Authorization'] = `Bearer ${token}`
    const res  = await fetch(`/api/public/upcoming?${params}`, { headers })
    const data = await res.json()
    if (res.status === 404)  { disabled.value = true; return }
    if (res.status === 403 && data.error === 'disabled') { disabled.value = true; return }
    if (res.status === 401 && data.error === 'password_required') { needPassword.value = true; return }
    if (res.status === 403 && data.error === 'wrong_password') {
      needPassword.value = true; wrongPassword.value = true; return
    }
    if (!res.ok) { disabled.value = true; return }
    if (password) sessionStorage.setItem(SESSION_KEY, password)
    needPassword.value = false; wrongPassword.value = false
    language.value  = localStorage.getItem(LANG_KEY) || data.language || 'fr'
    events.value    = data.events   || {}
    libraries.value = data.libraries || []
    servers.value   = data.servers  || []
  } catch { disabled.value = true }
  finally { loading.value = false }
}

async function submitPassword() {
  wrongPassword.value = false
  await loadDashboard(passwordInput.value)
}

onMounted(() => loadDashboard(storedPassword()))
</script>

<style scoped>
.slide-down-enter-active, .slide-down-leave-active { transition: all 0.2s ease; }
.slide-down-enter-from, .slide-down-leave-to { opacity: 0; transform: translateY(-8px); }
</style>
