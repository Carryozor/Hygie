<template>
  <div class="space-y-6">
    <!-- Tab bar -->
    <div class="flex flex-wrap gap-1 bg-[var(--bg2)] border border-[var(--border)] rounded-xl p-1">
      <button
        v-for="tab in TABS"
        :key="tab.id"
        class="flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors"
        :class="activeTab === tab.id ? 'bg-[var(--accent)] text-white' : 'text-[var(--muted)] hover:text-white hover:bg-[var(--bg3)]'"
        @click="activeTab = tab.id"
      >
        <ServiceIcon v-if="tab.service" :name="tab.service" :size="14" :color="activeTab === tab.id ? '#fff' : undefined" />
        <i v-else :class="['fas', tab.faIcon, 'text-xs']" />
        <span>{{ tab.label }}</span>
      </button>
    </div>

    <div v-if="saved" role="status" class="bg-green-500/20 border border-green-500/30 text-green-400 rounded-lg px-4 py-3 text-sm flex items-center gap-2">
      <i class="fas fa-check-circle" /> {{ t('common.saved') }}
    </div>
    <div v-if="saveError" class="bg-red-500/20 border border-red-500/30 text-red-400 rounded-lg px-4 py-3 text-sm flex items-center gap-2">
      <i class="fas fa-exclamation-triangle" /> {{ saveError }}
    </div>

    <GeneralTab   v-show="activeTab === 'general'"    :form="form" />
    <ServersTab   v-show="activeTab === 'servers'"    :form="form" />
    <SeerrTab     v-show="activeTab === 'seerr'"     :form="form" />
    <RadarrTab    v-show="activeTab === 'radarr'"    :form="form" />
    <SonarrTab    v-show="activeTab === 'sonarr'"    :form="form" />
    <QbitTab      v-show="activeTab === 'qbit'"      :form="form" />
    <DiscordTab   v-show="activeTab === 'discord'"   :form="form" />

    <button
      v-if="activeTab !== 'servers'"
      :disabled="saving"
      class="w-full bg-[var(--accent)] hover:opacity-90 disabled:opacity-50 rounded-lg px-6 py-3 text-sm font-semibold transition-opacity"
      @click="save"
    >
      {{ saving ? t('common.saving') : t('common.save') }}
    </button>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useSettingsStore } from '@/stores/settings'
import ServiceIcon from '@/components/ui/ServiceIcon.vue'
import GeneralTab     from '@/components/settings/GeneralTab.vue'
import ServersTab     from '@/components/settings/ServersTab.vue'
import RadarrTab      from '@/components/settings/RadarrTab.vue'
import SonarrTab      from '@/components/settings/SonarrTab.vue'
import SeerrTab       from '@/components/settings/SeerrTab.vue'
import QbitTab        from '@/components/settings/QbitTab.vue'
import DiscordTab     from '@/components/settings/DiscordTab.vue'

const { t }     = useI18n()
const settings  = useSettingsStore()
const saving    = ref(false)
const saved     = ref(false)
const saveError = ref('')
const activeTab = ref('general')
const form      = ref({})

const TABS = computed(() => [
  { id: 'general', faIcon: 'fa-cog',    label: t('settings.tabs.general'), service: null },
  { id: 'servers', faIcon: 'fa-server', label: t('settings.tabs.servers'), service: null },
  { id: 'seerr',   faIcon: null,        label: t('settings.tabs.seerr'),   service: 'overseerr' },
  { id: 'radarr',  faIcon: null,        label: t('settings.tabs.radarr'),  service: 'radarr' },
  { id: 'sonarr',  faIcon: null,        label: t('settings.tabs.sonarr'),  service: 'sonarr' },
  { id: 'qbit',    faIcon: null,        label: t('settings.tabs.qbit'),    service: form.value.qbit_proxy_url ? 'qui' : 'qbittorrent' },
  { id: 'discord', faIcon: null,        label: t('settings.tabs.discord'), service: 'discord' },
])

function syncForm() {
  const s = settings.settings
  const b = k => s[k] === 'true' || s[k] === true
  form.value = {
    dry_run: b('dry_run'), log_level: s.log_level || 'INFO',
    max_parallel_library_scans: Number(s.max_parallel_library_scans || 3),
    scan_interval_minutes: Number(s.scan_interval_minutes || 360),
    deletion_check_interval_minutes: Number(s.deletion_check_interval_minutes || 60),
    deleted_retention_days: Number(s.deleted_retention_days || 30),
    log_retention_days: Number(s.log_retention_days || 30),
    job_history_retention_days: Number(s.job_history_retention_days || 30),
    backup_enabled: b('backup_enabled'), backup_interval_hours: Number(s.backup_interval_hours || 24),
    backup_retention_count: Number(s.backup_retention_count || 5), backup_path: s.backup_path || '',
    public_dashboard_enabled:  b('public_dashboard_enabled'),
    public_dashboard_slug:     s.public_dashboard_slug     || '',
    public_dashboard_password: s.public_dashboard_password || '',
    emby_leaving_soon_overlay: b('emby_leaving_soon_overlay'),
    emby_leaving_soon_collection: s.emby_leaving_soon_collection || '',
    emby_leaving_soon_days: Number(s.emby_leaving_soon_days || 30),
    plex_tv_token: s.plex_tv_token || '', plex_webhook_secret: s.plex_webhook_secret || '',
    plex_overlay_enabled: b('plex_overlay_enabled'),
    radarr_url: s.radarr_url || '', radarr_api_key: s.radarr_api_key || '',
    radarr_servers: s.radarr_servers || '[]',
    sonarr_url: s.sonarr_url || '', sonarr_api_key: s.sonarr_api_key || '',
    sonarr_servers: s.sonarr_servers || '[]',
    seerr_url: s.seerr_url || '', seerr_api_key: s.seerr_api_key || '', seerr_external_url: s.seerr_external_url || '',
    qbit_url: s.qbit_url || '', qbit_proxy_url: s.qbit_proxy_url || '',
    qbit_user: s.qbit_user || '', qbit_password: s.qbit_password || '',
    qbit_action: s.qbit_action || '', qbit_tag: s.qbit_tag || '',
    discord_webhook: s.discord_webhook || '', discord_webhook_alerts: s.discord_webhook_alerts || '',
    discord_notif_thresholds: s.discord_notif_thresholds || '30,14,7,3,1',
    discord_alert_deletion_error: s.discord_alert_deletion_error || 'false',
    discord_alert_deletion_error_mention: s.discord_alert_deletion_error_mention || '',
    discord_alert_deletion_error_msg: s.discord_alert_deletion_error_msg || '',
    discord_alert_scan_failure: s.discord_alert_scan_failure || 'false',
    discord_alert_scan_failure_mention: s.discord_alert_scan_failure_mention || '',
    discord_alert_scan_failure_msg: s.discord_alert_scan_failure_msg || '',
    discord_alert_seerr_failure: s.discord_alert_seerr_failure || 'false',
    discord_alert_seerr_failure_mention: s.discord_alert_seerr_failure_mention || '',
    discord_alert_seerr_failure_msg: s.discord_alert_seerr_failure_msg || '',
    discord_alert_error_threshold: Number(s.discord_alert_error_threshold || 0),
  }
}

watch(() => settings.settings, () => { if (!saving.value) syncForm() }, { deep: true })

async function save() {
  saving.value = true; saved.value = false; saveError.value = ''
  try {
    const f = form.value
    await settings.save({
      dry_run: String(f.dry_run), log_level: f.log_level,
      max_parallel_library_scans: String(f.max_parallel_library_scans),
      scan_interval_minutes: String(f.scan_interval_minutes),
      deletion_check_interval_minutes: String(f.deletion_check_interval_minutes),
      deleted_retention_days: String(f.deleted_retention_days),
      log_retention_days: String(f.log_retention_days),
      job_history_retention_days: String(f.job_history_retention_days),
      backup_enabled: String(f.backup_enabled), backup_interval_hours: String(f.backup_interval_hours),
      backup_retention_count: String(f.backup_retention_count), backup_path: f.backup_path,
      public_dashboard_enabled:  String(f.public_dashboard_enabled),
      public_dashboard_slug:     f.public_dashboard_slug || '',
      public_dashboard_password: f.public_dashboard_password || '',
      emby_leaving_soon_overlay: String(f.emby_leaving_soon_overlay),
      emby_leaving_soon_collection: f.emby_leaving_soon_collection,
      emby_leaving_soon_days: String(f.emby_leaving_soon_days),
      plex_tv_token: f.plex_tv_token, plex_webhook_secret: f.plex_webhook_secret,
      plex_overlay_enabled: String(f.plex_overlay_enabled),
      radarr_url: f.radarr_url, radarr_api_key: f.radarr_api_key,
      radarr_servers: f.radarr_servers,
      sonarr_url: f.sonarr_url, sonarr_api_key: f.sonarr_api_key,
      sonarr_servers: f.sonarr_servers,
      seerr_url: f.seerr_url, seerr_api_key: f.seerr_api_key, seerr_external_url: f.seerr_external_url,
      qbit_url: f.qbit_url, qbit_proxy_url: f.qbit_proxy_url,
      qbit_user: f.qbit_user, qbit_password: f.qbit_password,
      qbit_action: f.qbit_action, qbit_tag: f.qbit_tag,
      discord_webhook: f.discord_webhook, discord_webhook_alerts: f.discord_webhook_alerts,
      discord_notif_thresholds: f.discord_notif_thresholds,
      discord_alert_deletion_error: f.discord_alert_deletion_error,
      discord_alert_deletion_error_mention: f.discord_alert_deletion_error_mention,
      discord_alert_deletion_error_msg: f.discord_alert_deletion_error_msg,
      discord_alert_scan_failure: f.discord_alert_scan_failure,
      discord_alert_scan_failure_mention: f.discord_alert_scan_failure_mention,
      discord_alert_scan_failure_msg: f.discord_alert_scan_failure_msg,
      discord_alert_seerr_failure: f.discord_alert_seerr_failure,
      discord_alert_seerr_failure_mention: f.discord_alert_seerr_failure_mention,
      discord_alert_seerr_failure_msg: f.discord_alert_seerr_failure_msg,
      discord_alert_error_threshold: String(f.discord_alert_error_threshold),
    })
    saved.value = true
    setTimeout(() => { saved.value = false }, 3000)
  } catch (e) {
    const msg = e?.response?.data?.detail || e?.message || 'Erreur inconnue'
    saveError.value = `Échec de la sauvegarde : ${msg}`
    setTimeout(() => { saveError.value = '' }, 6000)
  } finally { saving.value = false }
}

onMounted(async () => {
  await settings.fetch()
  syncForm()
})
</script>
