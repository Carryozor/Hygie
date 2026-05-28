// ─── Settings field lists ─────────────────────────────────────────────────────
const SETTINGS_FORM_FIELDS = [
  'radarr_url','radarr_api_key',
  'sonarr_url','sonarr_api_key',
  'seerr_url','seerr_api_key','seerr_external_url',
  'qbit_url','qbit_proxy_url','qbit_user','qbit_password',
  'emby_leaving_soon_collection','emby_leaving_soon_days','qbit_tag',
  'discord_webhook','discord_webhook_alerts','discord_notif_thresholds','discord_alert_error_threshold',
  'discord_alert_deletion_error_mention','discord_alert_deletion_error_msg',
  'discord_alert_scan_failure_mention','discord_alert_scan_failure_msg',
  'discord_alert_seerr_failure_mention','discord_alert_seerr_failure_msg',
  'max_parallel_library_scans',
  'backup_path','backup_interval_hours','backup_retention_count',
];

// ─── Media Servers ────────────────────────────────────────────────────────────
let _mediaServers = [];

async function loadMediaServers() {
  try {
    _mediaServers = await api('/api/settings/media-servers');
    renderMediaServers();
    updateMediaServerIconFromServers();
    const hasEmby = _mediaServers.some(s => s.enabled && (s.type === 'emby' || s.type === 'jellyfin'));
    const col = document.getElementById('media-emby-only');
    if (col) col.style.display = hasEmby ? 'block' : 'none';
  } catch(e) {}
}

function renderMediaServers() {
  const container = document.getElementById('media-servers-list');
  if (!container) return;
  if (!_mediaServers.length) {
    container.innerHTML = '<div style="text-align:center;padding:20px;color:var(--muted);font-size:13px">Aucun serveur configuré — cliquez "Ajouter" ci-dessous</div>';
    return;
  }
  container.innerHTML = _mediaServers.map((s, i) => {
    const type = s.type || '';
    const EMBY_URL = '/static/img/icons/emby.png';
    const JF_URL = '/static/img/icons/jellyfin.png';
    const iconHtml = type === 'emby'
      ? `<img src="${EMBY_URL}" width="28" height="28" style="border-radius:6px">`
      : type === 'jellyfin'
      ? `<img src="${JF_URL}" width="28" height="28" style="border-radius:6px">`
      : `<i class="fas fa-photo-film" style="font-size:18px;color:#a78bfa;width:28px;text-align:center"></i>`;
    const typeBadge = type === 'emby'
      ? `<span style="font-size:10px;padding:1px 6px;border-radius:8px;background:#52b04020;color:#52b040">Emby</span>`
      : type === 'jellyfin'
      ? `<span style="font-size:10px;padding:1px 6px;border-radius:8px;background:#8b5cf620;color:#a78bfa">Jellyfin</span>`
      : type === 'unknown'
      ? `<span style="font-size:10px;padding:1px 6px;border-radius:8px;background:#ffffff10;color:var(--muted)">Inconnu</span>`
      : `<span style="font-size:10px;padding:1px 6px;border-radius:8px;background:#ffffff08;color:var(--muted);font-style:italic">Non testé</span>`;
    return `<div class="card" style="padding:16px">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">
        <div style="width:36px;height:36px;border-radius:8px;background:#0f1117;display:flex;align-items:center;justify-content:center;flex-shrink:0">${iconHtml}</div>
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <input class="input" style="width:160px;font-size:13px;font-weight:600" value="${escapeHtml(s.name||'')}"
              onchange="updateServerField('${s.id}','name',this.value)" placeholder="Nom du serveur">
            ${typeBadge}
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:8px;flex-shrink:0">
          <label class="toggle-wrap" title="${s.enabled ? 'Activé — cliquer pour désactiver' : 'Désactivé — cliquer pour activer'}">
            <input type="checkbox" ${s.enabled ? 'checked' : ''} onchange="toggleMediaServer('${s.id}',this.checked)">
            <div class="toggle-track"></div><div class="toggle-thumb"></div>
          </label>
          <button class="btn btn-ghost" style="padding:5px 8px;font-size:11px;color:#ef4444" onclick="removeMediaServer('${s.id}')" title="Supprimer"><i class="fas fa-trash"></i></button>
        </div>
      </div>
      <div style="display:flex;flex-direction:column;gap:8px">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
          <div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:3px">URL interne</label>
            <input class="input" style="font-size:12px" value="${escapeHtml(s.url||'')}" placeholder="http://emby:8096"
              onchange="updateServerField('${s.id}','url',this.value)"></div>
          <div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:3px">Clé API</label>
            <input class="input" type="password" style="font-size:12px" value="${escapeHtml(s.api_key||'')}" placeholder="••••••••"
              onchange="updateServerField('${s.id}','api_key',this.value)"></div>
        </div>
        <div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:3px">URL externe (affiches)</label>
          <input class="input" style="font-size:12px" value="${escapeHtml(s.ext_url||'')}" placeholder="https://emby.mondomaine.fr"
            onchange="updateServerField('${s.id}','ext_url',this.value)"></div>
        <button class="btn btn-ghost" style="align-self:flex-start;font-size:12px" onclick="testMediaServer('${s.id}',this)">
          <i class="fas fa-plug"></i>Tester
        </button>
      </div>
    </div>`;
  }).join('');
}

async function addMediaServer() {
  try {
    const result = await api('/api/settings/media-servers', 'POST', {name: 'Nouveau serveur', url: '', api_key: '', ext_url: '', enabled: true});
    _mediaServers = result.servers;
    renderMediaServers();
  } catch(e) { toast('Erreur ajout serveur','error'); }
}

async function removeMediaServer(id) {
  try { await showConfirm({ title: 'Supprimer ce serveur ?', body: 'Ce serveur sera retiré de la liste. Les bibliothèques associées ne seront plus scannées.', icon: 'server', color: '#ef4444', okLabel: 'Supprimer' }); } catch(e) { return; }
  try {
    const result = await api('/api/settings/media-servers/' + id, 'DELETE');
    _mediaServers = result.servers;
    renderMediaServers();
    updateMediaServerIconFromServers();
  } catch(e) { toast('Erreur suppression','error'); }
}

let _serverUpdateTimers = {};
function updateServerField(id, field, value) {
  clearTimeout(_serverUpdateTimers[id + field]);
  _serverUpdateTimers[id + field] = setTimeout(async () => {
    try {
      const result = await api('/api/settings/media-servers/' + id, 'PUT', {[field]: value});
      _mediaServers = result.servers;
    } catch(e) {}
  }, 600);
}

async function toggleMediaServer(id, enabled) {
  try {
    const result = await api('/api/settings/media-servers/' + id, 'PUT', {enabled});
    _mediaServers = result.servers;
    updateMediaServerIconFromServers();
    const hasEmby = _mediaServers.some(s => s.enabled && (s.type === 'emby' || s.type === 'jellyfin'));
    const col = document.getElementById('media-emby-only');
    if (col) col.style.display = hasEmby ? 'block' : 'none';
  } catch(e) {}
}

async function testMediaServer(id, btn) {
  const origHtml = btn.innerHTML;
  btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>Test...';
  btn.disabled = true;
  try {
    const r = await api('/api/settings/media-servers/' + id + '/test', 'POST');
    toast(r.message || 'OK', r.ok ? 'success' : 'error');
    if (r.ok) {
      const s = _mediaServers.find(x => String(x.id) === String(id));
      if (s) s.type = r.server_type;
      renderMediaServers();
      updateMediaServerIconFromServers();
      const hasEmby = _mediaServers.some(sv => sv.enabled && sv.type === 'emby');
      const col = document.getElementById('media-emby-only');
      if (col) col.style.display = hasEmby ? 'block' : 'none';
    }
  } catch(e) { toast('Erreur connexion','error'); }
  btn.innerHTML = origHtml;
  btn.disabled = false;
}

// ─── Settings ─────────────────────────────────────────────────────────────────
let _settingsLoaded=false, _settingsDirty=false, _settingsListenersAttached=false, _activeSettingsTab='general';

function markSettingsDirty() { _settingsDirty=true; }
function switchSettingsTab(tab) {
  _activeSettingsTab = tab;
  document.querySelectorAll('.settings-tab').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('[id^="spanel-"]').forEach(el => el.style.display = 'none');
  const btn = document.querySelector('[data-stab="' + tab + '"]');
  if (btn) btn.classList.add('active');
  const panel = document.getElementById('spanel-' + tab);
  if (panel) panel.style.display = 'block';
  if (tab === 'media') loadMediaServers();
}

function updateIntervalPreview(id) {
  const val = parseInt(document.getElementById(id + '_interval_value')?.value) || 1;
  const unit = document.getElementById(id + '_interval_unit')?.value || 'h';
  const el = document.getElementById(id + '_interval_preview');
  if (!el) return;
  let text;
  if (unit === 'h') {
    text = '→ toutes les ' + val + 'h';
  } else {
    if (val < 60) { text = '→ toutes les ' + val + ' min'; }
    else {
      const h = Math.floor(val / 60), m = val % 60;
      text = '→ toutes les ' + h + 'h' + (m > 0 ? m + 'min' : '');
    }
  }
  el.textContent = text;
}

async function updateMediaServerIconFromServers() {
  try {
    const servers = await api('/api/settings/media-servers');
    const enabledTypes = [...new Set(servers.filter(s => s.enabled).map(s => s.type).filter(t => t && t !== 'unknown' && t !== ''))];
    if (enabledTypes.length === 1) {
      updateMediaServerIcon(enabledTypes[0]);
    } else if (enabledTypes.length > 1) {
      updateMediaServerIcon('mixed');
    } else {
      updateMediaServerIcon('');
    }
  } catch(e) {}
}

function updateMediaServerIcon(type) {
  const iconWrap = document.getElementById('stab-media-icon-wrap');
  const headerLogo = document.getElementById('media-server-logo');
  const pill = document.getElementById('stab-media-pill');
  const detected = document.getElementById('media-server-detected');
  const embyOnly = document.getElementById('media-emby-only');
  const nonEmbyNotice = document.getElementById('media-non-emby-notice');
  const nonEmbyMsg = document.getElementById('media-non-emby-msg');
  if (!iconWrap) return;
  const EMBY_ICON = '/static/img/icons/emby.png';
  const JF_ICON = '/static/img/icons/jellyfin.png';
  const GENERIC = '<i class="fas fa-photo-film" style="font-size:13px;color:#a78bfa"></i>';
  const GENERIC_HEADER = '<i class="fas fa-photo-film" style="font-size:20px;background:linear-gradient(135deg,#6366f1,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent"></i>';
  if (type === 'emby') {
    if (iconWrap) iconWrap.innerHTML = '<img src="' + EMBY_ICON + '" width="16" height="16" style="border-radius:3px">';
    if (headerLogo) headerLogo.innerHTML = '<img src="' + EMBY_ICON + '" style="width:38px;height:38px;object-fit:contain;border-radius:8px">';
    if (pill) { pill.textContent = 'Emby'; pill.style.cssText = 'display:inline;font-size:9px;padding:1px 5px;border-radius:10px;font-weight:600;background:#52b04025;color:#52b040'; }
    if (detected) { detected.textContent = '✓ Emby détecté'; detected.style.color = '#52b040'; detected.style.fontStyle = 'normal'; }
    if (embyOnly) embyOnly.style.display = 'block';
    if (nonEmbyNotice) nonEmbyNotice.style.display = 'none';
  } else if (type === 'jellyfin') {
    if (iconWrap) iconWrap.innerHTML = '<img src="' + JF_ICON + '" width="16" height="16" style="border-radius:3px">';
    if (headerLogo) headerLogo.innerHTML = '<img src="' + JF_ICON + '" style="width:38px;height:38px;object-fit:contain;border-radius:8px">';
    if (pill) { pill.textContent = 'Jellyfin'; pill.style.cssText = 'display:inline;font-size:9px;padding:1px 5px;border-radius:10px;font-weight:600;background:#8b5cf625;color:#a78bfa'; }
    if (detected) { detected.textContent = '✓ Jellyfin détecté'; detected.style.color = '#a78bfa'; detected.style.fontStyle = 'normal'; }
    if (embyOnly) embyOnly.style.display = 'block';
    if (nonEmbyNotice) nonEmbyNotice.style.display = 'none';
  } else if (type === 'unknown') {
    if (iconWrap) iconWrap.innerHTML = GENERIC;
    if (headerLogo) headerLogo.innerHTML = GENERIC_HEADER;
    if (pill) pill.style.display = 'none';
    if (detected) { detected.textContent = 'Serveur non reconnu — fonctionnalités Collection/Overlay disponibles avec Emby uniquement'; detected.style.color = 'var(--muted)'; detected.style.fontStyle = 'italic'; }
    if (embyOnly) embyOnly.style.display = 'none';
    if (nonEmbyNotice) { nonEmbyNotice.style.display = 'block'; if (nonEmbyMsg) nonEmbyMsg.innerHTML = '⚠️ <strong>Serveur non reconnu</strong> — Les fonctionnalités Collection et Overlay d\'affiches sont disponibles uniquement avec Emby.'; nonEmbyNotice.style.color = '#94a3b8'; }
  } else if (type === 'mixed') {
    const SPLIT_TAB = `<div style="width:18px;height:18px;border-radius:4px;position:relative;overflow:hidden;flex-shrink:0"><img src="${EMBY_ICON}" style="position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;clip-path:inset(0 50% 0 0)"><img src="${JF_ICON}" style="position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;clip-path:inset(0 0 0 50%)"></div>`;
    const SPLIT_HDR = `<div style="width:44px;height:44px;border-radius:10px;position:relative;overflow:hidden"><img src="${EMBY_ICON}" style="position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;clip-path:inset(0 50% 0 0)"><img src="${JF_ICON}" style="position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;clip-path:inset(0 0 0 50%)"></div>`;
    if (iconWrap) iconWrap.innerHTML = SPLIT_TAB;
    if (headerLogo) headerLogo.innerHTML = SPLIT_HDR;
    if (pill) { pill.textContent = 'Multi'; pill.style.cssText = 'display:inline;font-size:9px;padding:1px 5px;border-radius:10px;font-weight:600;background:#6366f125;color:#818cf8'; }
    if (detected) { detected.textContent = 'Emby + Jellyfin — plusieurs serveurs actifs'; detected.style.color = '#818cf8'; detected.style.fontStyle = 'normal'; }
    if (embyOnly) embyOnly.style.display = 'block';
    if (nonEmbyNotice) nonEmbyNotice.style.display = 'none';
  } else {
    if (iconWrap) iconWrap.innerHTML = GENERIC;
    if (headerLogo) headerLogo.innerHTML = GENERIC_HEADER;
    if (pill) pill.style.display = 'none';
    if (detected) { detected.textContent = 'Non encore testé — cliquez Tester pour détecter'; detected.style.color = 'var(--muted)'; detected.style.fontStyle = 'italic'; }
    if (embyOnly) embyOnly.style.display = 'block';
    if (nonEmbyNotice) nonEmbyNotice.style.display = 'none';
  }
}

function onBackupEnabledChange() {
  const enabled = document.getElementById('backup_enabled')?.checked;
  const badge = document.getElementById('backup-disabled-badge');
  if (badge) badge.style.display = enabled ? 'none' : 'block';
}

function toggleAlertCustom(name) {
  const panel = document.getElementById(`alert-custom-${name}`);
  if (!panel) return;
  const open = panel.style.display !== 'none';
  panel.style.display = open ? 'none' : 'block';
}

async function loadSettings(force=false) {
  if (_settingsLoaded && _settingsDirty && !force) return;
  try {
    const s = await api('/api/settings/');
    SETTINGS_FORM_FIELDS.forEach(f => {
      const el=document.getElementById(f); if(el) el.value=s[f]||'';
    });
    document.getElementById('dry-run-toggle').checked = s.dry_run==='true';
    const _sdr = document.getElementById('sidebar-dry-run');
    if (_sdr) _sdr.checked = s.dry_run==='true';
    _updateSidebarDryRunStyle(s.dry_run==='true');
    const scanMin = parseInt(s.scan_interval_minutes || '360');
    _scanIntervalMin = scanMin;
    _delIntervalMin  = parseInt(s.deletion_check_interval_minutes || '60');
    const scanInH = scanMin % 60 === 0;
    const svEl = document.getElementById('scan_interval_value');
    const suEl = document.getElementById('scan_interval_unit');
    if (svEl) svEl.value = scanInH ? scanMin / 60 : scanMin;
    if (suEl) suEl.value = scanInH ? 'h' : 'm';
    updateIntervalPreview('scan');
    const delMin = parseInt(s.deletion_check_interval_minutes || '60');
    const delInH = delMin % 60 === 0;
    const dvEl = document.getElementById('deletion_check_interval_value');
    const duEl = document.getElementById('deletion_check_interval_unit');
    if (dvEl) dvEl.value = delInH ? delMin / 60 : delMin;
    if (duEl) duEl.value = delInH ? 'h' : 'm';
    updateIntervalPreview('deletion_check');
    updateMediaServerIconFromServers();
    switchSettingsTab(_activeSettingsTab);
    if (_activeSettingsTab === 'media') loadMediaServers();
    if(document.getElementById('deleted_retention_days')) document.getElementById('deleted_retention_days').value = s.deleted_retention_days||'90';
    const qa = document.getElementById('qbit_action'); if(qa) qa.value = s.qbit_action||'tag_only';
    const ov = document.getElementById('emby_leaving_soon_overlay'); if(ov) ov.checked = s.emby_leaving_soon_overlay==='true';
    ['discord_alert_deletion_error','discord_alert_scan_failure','discord_alert_seerr_failure'].forEach(k => {
      const el = document.getElementById(k); if(el) el.checked = s[k]==='true';
    });
    const backupEnabledEl = document.getElementById('backup_enabled');
    if (backupEnabledEl) {
      backupEnabledEl.checked = s.backup_enabled !== 'false';
      onBackupEnabledChange();
    }
    const ls=document.getElementById('log_level'); if(ls) ls.value=s.log_level||'INFO';
    _settingsLoaded=true; _settingsDirty=false;
    if (!_settingsListenersAttached) {
      document.querySelectorAll('#page-settings input, #page-settings select').forEach(el => {
        el.addEventListener('input', markSettingsDirty);
        el.addEventListener('change', markSettingsDirty);
      });
      _settingsListenersAttached = true;
    }
  } catch(e) { toast('Erreur paramètres','error'); }
}

async function saveSettings() {
  const fields = SETTINGS_FORM_FIELDS;
  const body = {
    dry_run: document.getElementById('dry-run-toggle').checked ? 'true' : 'false',
    deleted_retention_days: document.getElementById('deleted_retention_days')?.value || '90',
    log_level: document.getElementById('log_level')?.value || 'INFO',
    qbit_action: document.getElementById('qbit_action')?.value || 'tag_only',
    emby_leaving_soon_overlay: document.getElementById('emby_leaving_soon_overlay')?.checked ? 'true' : 'false',
    discord_alert_deletion_error: document.getElementById('discord_alert_deletion_error')?.checked ? 'true' : 'false',
    discord_alert_scan_failure: document.getElementById('discord_alert_scan_failure')?.checked ? 'true' : 'false',
    discord_alert_seerr_failure: document.getElementById('discord_alert_seerr_failure')?.checked ? 'true' : 'false',
    backup_enabled: document.getElementById('backup_enabled')?.checked ? 'true' : 'false',
  };
  const scanV = parseInt(document.getElementById('scan_interval_value')?.value) || 6;
  const scanU = document.getElementById('scan_interval_unit')?.value || 'h';
  body.scan_interval_minutes = String(scanU === 'h' ? scanV * 60 : scanV);
  const delV = parseInt(document.getElementById('deletion_check_interval_value')?.value) || 1;
  const delU = document.getElementById('deletion_check_interval_unit')?.value || 'h';
  body.deletion_check_interval_minutes = String(delU === 'h' ? delV * 60 : delV);
  fields.forEach(f=>{ const el=document.getElementById(f); if(el) body[f]=el.value; });
  try { await api('/api/settings/','POST',body); _settingsDirty=false; toast('Paramètres enregistrés','success'); }
  catch(e) { toast('Erreur sauvegarde','error'); }
}

function collectFormValues() {
  const out={};
  SETTINGS_FORM_FIELDS.forEach(f=>{ const el=document.getElementById(f); if(el) out[f]=el.value||''; });
  return out;
}

async function testConn(service) {
  try { await api('/api/settings/','POST',collectFormValues()); _settingsDirty=false; } catch(e) {}
  try { const r=await api(`/api/libraries/test/${service}`,'POST'); toast(r.message||'OK',r.ok?'success':'error'); }
  catch(e) { toast('Erreur connexion','error'); }
}

// ─── Discord Mappings ─────────────────────────────────────────────────────────
let _discordMappings = {};

async function loadDiscordMappings() {
  const box = document.getElementById('discord-mappings-list');
  if (!box) return;
  box.innerHTML = '<div style="font-size:12px;color:var(--muted)">Chargement...</div>';
  try {
    const users = await api('/api/seerr-rules/users');
    const saved = await api('/api/seerr-rules/discord-mappings');
    const savedMap = {};
    for (const s of saved) savedMap[s.seerr_user_id] = s.discord_id || '';
    if (!users.length) {
      box.innerHTML = '<div style="font-size:12px;color:var(--muted)">Aucun utilisateur Seerr trouvé (vérifiez la configuration Seerr)</div>';
      return;
    }
    box.innerHTML = users.map(u => {
      const savedId = savedMap[u.id] || u.discord_id || '';
      const autoTag = u.discord_id ? `<span style="font-size:10px;color:#10b981;margin-left:4px">auto-détecté</span>` : '';
      return `<div style="display:flex;align-items:center;gap:8px;padding:6px;background:#0a0c14;border-radius:6px;border:1px solid var(--border)">
        <div style="flex:1;min-width:0">
          <div style="font-size:12px;font-weight:500;color:#e2e8f0">${u.username}</div>
          <div style="font-size:10px;color:var(--muted)">ID Seerr: ${u.id}${autoTag}</div>
        </div>
        <input class="input" style="width:160px;font-size:12px" placeholder="ID Discord (ex: 1234567890)"
               value="${escapeHtml(savedId)}" id="discord-map-${u.id}"
               data-uid="${u.id}" data-uname="${escapeHtml(u.username)}"
               oninput="saveDiscordMappingFromEl(this)">
      </div>`;
    }).join('');
  } catch(e) {
    box.innerHTML = `<div style="font-size:12px;color:var(--muted)">Erreur chargement (Seerr configuré ?)</div>`;
  }
}

let _discordSaveTimers = {};
function saveDiscordMappingFromEl(el) {
  saveDiscordMapping(parseInt(el.dataset.uid), el.dataset.uname, el.value);
}
function saveDiscordMapping(userId, username, discordId) {
  clearTimeout(_discordSaveTimers[userId]);
  _discordSaveTimers[userId] = setTimeout(async () => {
    try {
      await api('/api/seerr-rules/discord-mappings', 'POST', {
        seerr_user_id: userId,
        seerr_username: username,
        discord_id: discordId.trim()
      });
      const input = document.getElementById(`discord-map-${userId}`);
      if (input) {
        input.style.borderColor = '#10b981';
        setTimeout(() => { input.style.borderColor = ''; }, 1500);
      }
    } catch(e) { /* silent */ }
  }, 800);
}

// ─── Backup ───────────────────────────────────────────────────────────────────
async function triggerManualBackup() {
  try {
    const r = await api('/api/backup', 'POST');
    toast(`Backup créé : ${r.filename}`, 'success');
    loadBackupList();
  } catch(e) { toast('Erreur backup : ' + (e.message||''), 'error'); }
}

async function loadBackupList() {
  const wrap = document.getElementById('backup-list-wrap');
  const box  = document.getElementById('backup-list');
  if (!wrap || !box) return;
  try {
    const files = await api('/api/backup');
    wrap.style.display = 'block';
    if (!files.length) {
      box.innerHTML = '<div style="font-size:11px;color:var(--muted);padding:6px">Aucun backup</div>';
      return;
    }
    box.innerHTML = files.map(f => {
      const dt = new Date(f.created_at).toLocaleString();
      const kb = Math.round(f.size_bytes / 1024);
      return `<div style="display:flex;align-items:center;gap:8px;padding:6px 8px;background:#0a0c14;border-radius:6px;font-size:11px">
        <i class="fas fa-database" style="color:var(--muted);flex-shrink:0"></i>
        <span style="flex:1;color:#e2e8f0">${escapeHtml(f.filename)}</span>
        <span style="color:var(--muted)">${kb} Ko</span>
        <span style="color:var(--muted)">${dt}</span>
        <button class="btn btn-ghost" style="padding:2px 6px;font-size:10px;color:#ef4444" onclick="deleteBackup('${escapeHtml(f.filename)}')"><i class="fas fa-trash"></i></button>
      </div>`;
    }).join('');
  } catch(e) { toast('Erreur liste backups','error'); }
}

async function deleteBackup(filename) {
  try {
    await showConfirm({ title: 'Supprimer ce backup ?', body: filename, icon: 'database', color: '#ef4444', okLabel: 'Supprimer' });
  } catch(e) { return; }
  try {
    await api(`/api/backup/${encodeURIComponent(filename)}`, 'DELETE');
    toast('Backup supprimé', 'success');
    loadBackupList();
  } catch(e) { toast('Erreur suppression', 'error'); }
}
