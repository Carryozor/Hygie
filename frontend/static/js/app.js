// ─── Security utilities ───────────────────────────────────────────────────────
function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#x27;');
}


// ─── Queue urgency color ──────────────────────────────────────────────────────
function daysColorStyle(days) {
  if (days < 3)  return 'color:#ef4444;animation:hygie-glow 1.5s ease-in-out infinite';
  if (days < 7)  return 'color:#ef4444';
  if (days < 14) return 'color:#f97316';
  if (days < 30) return 'color:#eab308';
  return 'color:var(--muted)';
}

// ─── Confirmation modal (remplace les confirm() natifs) ──────────────────────
let _confirmResolve = () => {};
let _confirmReject  = () => {};

function showConfirm({ title, body, detail, icon, color, okLabel, okClass }) {
  icon = icon || 'triangle-exclamation';
  color = color || '#ef4444';
  okLabel = okLabel || 'Confirmer';
  okClass = okClass || 'btn-danger';
  return new Promise((resolve, reject) => {
    _confirmResolve = () => { _closeConfirm(); resolve(true); };
    _confirmReject  = () => { _closeConfirm(); reject(false); };
    document.getElementById('confirm-title').textContent = title;
    document.getElementById('confirm-body').innerHTML = body || '';
    const detailEl = document.getElementById('confirm-detail');
    if (detail) { detailEl.innerHTML = detail; detailEl.style.display = 'block'; }
    else { detailEl.style.display = 'none'; }
    const wrap = document.getElementById('confirm-icon-wrap');
    const ico  = document.getElementById('confirm-icon');
    wrap.style.background = color + '20';
    ico.className = 'fas fa-' + icon;
    ico.style.color = color;
    const okBtn = document.getElementById('confirm-ok-btn');
    okBtn.textContent = okLabel;
    okBtn.className = 'btn ' + okClass;
    okBtn.style.padding = '8px 20px';
    document.getElementById('modal-confirm').style.display = 'flex';
    setTimeout(() => okBtn.focus(), 50);
  });
}

function _updateSidebarDryRunStyle(enabled) {
  const wrap  = document.getElementById('sidebar-dry-run-wrap');
  const icon  = document.getElementById('sidebar-dry-run-icon');
  const label = document.getElementById('sidebar-dry-run-label');
  if (!wrap) return;
  if (enabled) {
    wrap.style.background   = '#f59e0b18';
    wrap.style.borderColor  = '#f59e0b40';
    if (icon)  icon.style.color  = '#f59e0b';
    if (label) label.style.color = '#f59e0b';
  } else {
    wrap.style.background   = 'var(--bg3)';
    wrap.style.borderColor  = 'var(--border)';
    if (icon)  icon.style.color  = 'var(--muted)';
    if (label) label.style.color = 'var(--muted)';
  }
}
async function toggleSidebarDryRun(enabled) {
  try {
    await api('/api/settings', 'POST', { dry_run: enabled ? 'true' : 'false' });
    _updateSidebarDryRunStyle(enabled);
    // Sync settings page toggle if open
    const settingsToggle = document.getElementById('dry-run-toggle');
    if (settingsToggle) settingsToggle.checked = enabled;
    toast(enabled ? 'Dry Run activé — aucune suppression réelle' : 'Dry Run désactivé', enabled ? 'warn' : 'info');
  } catch(e) { toast('Erreur','error'); }
}

function _closeConfirm() {
  document.getElementById('modal-confirm').style.display = 'none';
}
document.addEventListener('keydown', e => {
  if (document.getElementById('modal-confirm') && document.getElementById('modal-confirm').style.display !== 'none') {
    if (e.key === 'Enter') { e.preventDefault(); _confirmResolve(); }
    if (e.key === 'Escape') _confirmReject();
  }
});

// ─── Auth ─────────────────────────────────────────────────────────────────────

let _token = localStorage.getItem('hygie_token') || '';
function setToken(t) { _token = t; localStorage.setItem('hygie_token', t); }
function clearToken() { _token = ''; localStorage.removeItem('hygie_token'); }

async function api(url, method = 'GET', body = null) {
  const headers = { 'Content-Type': 'application/json' };
  if (_token) headers['Authorization'] = `Bearer ${_token}`;
  const opts = { method, headers };
  if (body) opts.body = JSON.stringify(body);
  // Normalize URL: strip trailing slash before query string to avoid 307 redirects
  const qIdx = url.indexOf('?');
  if (qIdx === -1) { url = url.replace(/\/+$/, ''); }
  else { url = url.slice(0, qIdx).replace(/\/+$/, '') + url.slice(qIdx); }
  const r = await fetch(url, opts);
  if (r.status === 401) { showLoginScreen(); throw new Error('Unauthorized'); }
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  if (r.status === 204) return null;
  return r.json();
}

async function initAuth() {
  try {
    const status = await fetch('/api/auth/status').then(r => r.json());
    if (!status.setup_complete) { showSetupScreen(); return; }
    if (!_token) { showLoginScreen(); return; }
    const r = await fetch('/api/auth/me', { headers: { Authorization: `Bearer ${_token}` } });
    if (r.ok) { showApp((await r.json()).username); }
    else { clearToken(); showLoginScreen(); }
  } catch(e) { showLoginScreen(); }
}

function showSetupScreen() {
  document.getElementById('auth-screen').style.display = 'flex';
  document.getElementById('app-shell').style.display = 'none';
  document.getElementById('auth-title').textContent = 'Bienvenue sur Hygie';
  document.getElementById('auth-subtitle').textContent = 'Créez votre compte administrateur';
  document.getElementById('auth-btn').textContent = 'Créer le compte';
  document.getElementById('auth-btn').onclick = doSetup;
}
function showLoginScreen() {
  document.getElementById('auth-screen').style.display = 'flex';
  document.getElementById('app-shell').style.display = 'none';
  document.getElementById('auth-title').textContent = 'Connexion';
  document.getElementById('auth-subtitle').textContent = 'Connectez-vous à Hygie';
  document.getElementById('auth-btn').textContent = 'Se connecter';
  document.getElementById('auth-btn').onclick = doLogin;
}
function showApp(username) {
  document.getElementById('auth-screen').style.display = 'none';
  const shell = document.getElementById('app-shell');
  shell.style.display = 'flex';
  shell.style.flexDirection = 'row';
  shell.style.height = '100vh';
  shell.style.overflow = 'hidden';
  document.getElementById('user-display').textContent = username;
  showPage('dashboard');
  setTimeout(initWebSocket, 500);
  setTimeout(loadSchedulerInfo, 100);  // load bars immediately, don't wait for dashboard
  // Load version
  fetch('/api/version').then(r=>r.json()).then(v=>{
    const el = document.getElementById('app-version');
    if(el) el.textContent = `v${v.version}`;
  }).catch(()=>{});
}

async function doSetup() {
  const u = document.getElementById('auth-username').value.trim();
  const p = document.getElementById('auth-password').value;
  if (!u || !p) { authError('Remplissez tous les champs'); return; }
  if (p.length < 6) { authError('Mot de passe trop court (min 6 car.)'); return; }
  try {
    const r = await fetch('/api/auth/setup', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username:u, password:p}) });
    if (!r.ok) { authError((await r.json()).detail || 'Erreur'); return; }
    const d = await r.json(); setToken(d.token); showApp(d.username || u);
  } catch(e) { authError('Erreur réseau'); }
}
async function doLogin() {
  const u = document.getElementById('auth-username').value.trim();
  const p = document.getElementById('auth-password').value;
  if (!u || !p) { authError('Remplissez tous les champs'); return; }
  try {
    const r = await fetch('/api/auth/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: u, password: p})
    });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      authError(data.detail || 'Identifiants incorrects');
      return;
    }
    const data = await r.json();
    setToken(data.token);
    showApp(data.username || u);
    toast('Connecté !', 'success');
  } catch(e) { authError('Erreur réseau : ' + e.message); }
}
function authError(msg) {
  const el = document.getElementById('auth-error');
  el.textContent = msg; el.style.display = 'block';
}
document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && document.getElementById('auth-screen').style.display !== 'none') {
    document.getElementById('auth-btn')?.click();
  }
});

// ─── Navigation ───────────────────────────────────────────────────────────────
let currentPage = 'dashboard';
function showPage(page) {
  document.querySelectorAll('.page').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
  document.getElementById(`page-${page}`)?.classList.add('active');
  document.getElementById(`nav-${page}`)?.classList.add('active');
  currentPage = page;
  const loaders = { dashboard: loadDashboard, queue: () => { queueOffset=0; loadQueue(); },
    libraries: loadLibraries, settings: loadSettings, logs: loadLogs, jobs: loadJobs,
    calendar: loadCalendar, storage: loadStorage, ignored: loadIgnored };
  if (loaders[page]) loaders[page]();
}

// ─── Toast ────────────────────────────────────────────────────────────────────
function toast(msg, type='info') {
  const icons = {success:'check-circle',error:'triangle-exclamation',info:'circle-info',warn:'exclamation'};
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  const _icon = document.createElement('i');
  _icon.className = `fas fa-${icons[type]||'circle-info'}`;
  const _span = document.createElement('span');
  _span.textContent = msg;
  el.append(_icon, _span);
  document.getElementById('toast-wrap').prepend(el);
  setTimeout(() => { el.style.opacity='0'; el.style.transition='opacity .3s'; setTimeout(()=>el.remove(),300); }, 3500);
}

// ─── Dashboard ────────────────────────────────────────────────────────────────
async function loadDashboard() {
  try {
    const s = await api('/api/media/stats');
    document.getElementById('stat-pending').textContent = s.pending ?? 0;
    document.getElementById('stat-deleted').textContent = s.deleted ?? 0;
    document.getElementById('stat-excluded').textContent = s.excluded ?? 0;
    document.getElementById('stat-error').textContent = s.error ?? 0;
  } catch(e) {}
  try {
    const data = await api('/api/media/?status=pending&limit=8&sort=delete_at&dir=asc');
    const box = document.getElementById('upcoming-list');
    if (!data.items.length) {
      box.innerHTML = '<div style="text-align:center;padding:24px;color:var(--muted)"><i class="fas fa-circle-check" style="font-size:24px;color:#10b98180;display:block;margin-bottom:8px"></i>Aucun média en attente</div>';
      return;
    }
    box.innerHTML = data.items.map(m => {
      const _delDt = new Date(m.delete_at); const _now = new Date();
      const days = Math.max(0, Math.round((Date.UTC(_delDt.getUTCFullYear(),_delDt.getUTCMonth(),_delDt.getUTCDate()) - Date.UTC(_now.getUTCFullYear(),_now.getUTCMonth(),_now.getUTCDate())) / 86400000));
      const col = daysColorStyle(days);
      const icon = m.media_type==='Movie'?'🎬':'📺';
      const _title = escapeHtml(m.title);
      const _lib = escapeHtml(m.library_name || m.library_id);
      const poster = m.poster_url ? `<img src="${escapeHtml(m.poster_url)}" style="width:28px;height:42px;object-fit:cover;border-radius:3px;flex-shrink:0" onerror="this.style.display='none'">` : '';
      const req = m.seerr_username ? `<span style="font-size:11px;color:var(--muted)">👤 ${escapeHtml(m.seerr_username)}</span>` : '';
      return `<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)">
        ${poster}
        <div style="flex:1;min-width:0">
          <div style="color:#e2e8f0;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${icon} ${_title}</div>
          <div style="display:flex;gap:8px;margin-top:2px">${req}<span style="font-size:11px;color:var(--muted)">📚 ${_lib}</span></div>
        </div>
        <div style="text-align:right;flex-shrink:0">
          <div style="${col};font-weight:600;font-size:12px">${days<=0?'Imminent':`dans ${days}j`}</div>
          <div style="color:var(--muted);font-size:10px">${new Date(m.delete_at).toLocaleDateString('fr-FR',{day:'numeric',month:'short'})}</div>
        </div>
      </div>`;
    }).join('');
  } catch(e) {}
  try {
    const g = await api('/api/stats/global');
    const setEl = (id, val) => { const el=document.getElementById(id); if(el) el.textContent=val; };
    setEl('global-deleted', g.total_deleted ?? 0);
    setEl('global-ignored', g.total_ignored ?? 0);
    setEl('global-scans', g.total_scans ?? 0);
    // Mini bar chart for last 12 months
    const bar = document.getElementById('global-bar');
    if (bar && g.by_month && g.by_month.length) {
      const max = Math.max(...g.by_month.map(m=>m.deleted), 1);
      bar.innerHTML = g.by_month.slice(-12).map(m => {
        const h = Math.max(3, Math.round(m.deleted / max * 28));
        const label = m.month ? m.month.slice(5) : '';
        return `<div title="${label}: ${m.deleted} suppression(s)" style="width:6px;height:${h}px;background:var(--accent2);border-radius:2px 2px 0 0;opacity:.8;flex-shrink:0"></div>`;
      }).join('');
    }
  } catch(e) {}
  try {
    const s = await api('/api/settings/');
    const _drEnabled = s.dry_run === 'true';
    const _sdrt = document.getElementById('sidebar-dry-run');
    if (_sdrt) _sdrt.checked = _drEnabled;
    _updateSidebarDryRunStyle(_drEnabled);
    // Cache interval values so progress bars are accurate even before settings page is opened
    _scanIntervalMin = parseInt(s.scan_interval_minutes || '360');
    _delIntervalMin  = parseInt(s.deletion_check_interval_minutes || '60');
    loadSchedulerInfo();
  } catch(e) {}
}
function _fmtCountdown(diffMin) {
  if (diffMin <= 0) return 'Imminent';
  if (diffMin < 60) return `dans ${diffMin}min`;
  const h = Math.floor(diffMin / 60), m = diffMin % 60;
  return `dans ${h}h${m > 0 ? String(m).padStart(2,'0') : ''}`;
}

async function loadSchedulerInfo() {
  try {
    const [jobs, status] = await Promise.all([
      api('/api/scheduler/status'),
      api('/api/media/job-status').catch(() => ({})),
    ]);
    const scanJob = jobs.find(j => j.id === 'scan_job');
    const delJob  = jobs.find(j => j.id === 'deletion_job');

    // ── Scan bar ──
    const scanEl  = document.getElementById('scan-countdown');
    const scanBar = document.getElementById('scan-progress-bar');
    if (status.scan_running) {
      // Scan in progress — pulse animation + label
      if (scanEl) scanEl.textContent = 'En cours...';
      if (scanBar) {
        scanBar.style.width = '100%';
        scanBar.style.opacity = '0.6';
        scanBar.style.animation = 'pulse-bar 1.2s ease-in-out infinite';
      }
    } else {
      if (scanBar) { scanBar.style.animation = ''; scanBar.style.opacity = '1'; }
      if (scanJob?.next_run) {
        const remainMin = Math.max(0, Math.round((new Date(scanJob.next_run) - Date.now()) / 60000));
        const pct = _scanIntervalMin > 0
          ? Math.max(0, Math.min(100, Math.round((1 - remainMin / _scanIntervalMin) * 100)))
          : 0;
        if (scanEl) scanEl.textContent = _fmtCountdown(remainMin);
        if (scanBar) scanBar.style.width = Math.max(2, pct) + '%';
      }
    }

    // ── Deletion check bar ──
    const delEl  = document.getElementById('del-countdown');
    const delBar = document.getElementById('del-progress-bar');
    if (status.deletion_running) {
      if (delEl) delEl.textContent = 'En cours...';
      if (delBar) {
        delBar.style.width = '100%';
        delBar.style.opacity = '0.6';
        delBar.style.animation = 'pulse-bar 1.2s ease-in-out infinite';
      }
    } else {
      if (delBar) { delBar.style.animation = ''; delBar.style.opacity = '1'; }
      if (delJob?.next_run) {
        const remainMin = Math.max(0, Math.round((new Date(delJob.next_run) - Date.now()) / 60000));
        const pct = _delIntervalMin > 0
          ? Math.max(0, Math.min(100, Math.round((1 - remainMin / _delIntervalMin) * 100)))
          : 0;
        if (delEl) delEl.textContent = _fmtCountdown(remainMin);
        if (delBar) delBar.style.width = Math.max(2, pct) + '%';
      }
    }
  } catch(e) {}
}

// ─── Queue ────────────────────────────────────────────────────────────────────
let selectedIds = new Set();
let queuePageSize = 50;
let queueTotalItems = 0;
let queueOffset = 0;
let queueSortCol = 'delete_at';
let queueSortDir = 'asc';
try { const _s=JSON.parse(sessionStorage.getItem('hq_sort')||'{}'); if(_s.col){queueSortCol=_s.col;queueSortDir=_s.dir||'asc';} } catch(e){}
let _searchTimer = null;
let queueViewMode = 'list'; // 'list' | 'grid'

const SORT_COLS = ['title','library_name','seerr_username','added_date','last_played','delete_at','status'];

function getPageCount() { return Math.max(1, Math.ceil(queueTotalItems/queuePageSize)); }
function getCurrentPage() { return Math.floor(queueOffset/queuePageSize)+1; }

function toggleViewMode() {
  queueViewMode = queueViewMode === 'list' ? 'grid' : 'list';
  const btn = document.getElementById('view-toggle-btn');
  if (btn) btn.innerHTML = queueViewMode === 'grid'
    ? '<i class="fas fa-list"></i>Vue liste'
    : '<i class="fas fa-th-large"></i>Vue grille';
  loadQueue();
}

function debounceSearch() {
  clearTimeout(_searchTimer);
  _searchTimer = setTimeout(() => { queueOffset=0; loadQueue(); }, 350);
}

let _queueStatusFilter = '';
function setQueueTab(status) {
  _queueStatusFilter = status;
  // Update tab styles
  ['all','pending','deleted','error'].forEach(t => {
    const btn = document.getElementById('tab-'+t);
    if (btn) btn.className = 'tab-btn' + (status === (t==='all'?'':t) ? ' tab-active' : '');
  });
  queueOffset = 0;
  loadQueue();
}

function sortQueue(col) {
  if (queueSortCol===col) queueSortDir = queueSortDir==='asc'?'desc':'asc';
  else { queueSortCol=col; queueSortDir='asc'; }
  try { sessionStorage.setItem('hq_sort', JSON.stringify({col:queueSortCol,dir:queueSortDir})); } catch(e){}
  queueOffset=0; loadQueue();
}
function sortIcon(col) {
  if (queueSortCol!==col) return '<i class="fas fa-sort" style="opacity:.25;font-size:10px;margin-left:3px"></i>';
  return queueSortDir==='asc'
    ? '<i class="fas fa-sort-up" style="color:var(--accent2);font-size:10px;margin-left:3px"></i>'
    : '<i class="fas fa-sort-down" style="color:var(--accent2);font-size:10px;margin-left:3px"></i>';
}

async function loadQueue() {
  const status = _queueStatusFilter;
  const search = document.getElementById('queue-search').value.trim();
  selectedIds.clear(); updateBulkBar();
  try {
    let url = `/api/media/?limit=${queuePageSize}&offset=${queueOffset}&sort=${queueSortCol}&dir=${queueSortDir}`;
    if (status) url += `&status=${status}`;
    if (search) url += `&search=${encodeURIComponent(search)}`;
    const data = await api(url);
    queueTotalItems = data.total;
    const pc = getPageCount(), cp = getCurrentPage();
    document.getElementById('queue-count').innerHTML = `${data.total} entrée${data.total>1?'s':''} &nbsp;·&nbsp; Page ${cp}/${pc}`;
    document.getElementById('btn-prev').disabled = cp<=1;
    document.getElementById('btn-next').disabled = cp>=pc;
    SORT_COLS.forEach(col => {
      const el = document.getElementById(`si-${col}`);
      if (el) el.innerHTML = sortIcon(col);
    });
    const tbody = document.getElementById('queue-tbody');
    if (!data.items.length) {
      tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;padding:40px;color:var(--muted)">${search?'Aucun résultat pour "'+escapeHtml(search)+'"':'Aucun média'}</td></tr>`;
      document.getElementById('chk-all').checked = false;
      return;
    }
    if (queueViewMode === 'grid') {
      // Hide table, show grid
      document.getElementById('queue-table-wrap').style.display = 'none';
      let grid = document.getElementById('queue-grid');
      if (!grid) {
        grid = document.createElement('div');
        grid.id = 'queue-grid';
        document.getElementById('queue-table-wrap').parentNode.insertBefore(grid, document.getElementById('queue-table-wrap'));
      }
      grid.style.display = 'grid';
      grid.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:14px;padding:16px';
      grid.innerHTML = data.items.map(m => {
        const _delDt2 = new Date(m.delete_at); const _now2 = new Date();
        const days = Math.max(0, Math.round((Date.UTC(_delDt2.getUTCFullYear(),_delDt2.getUTCMonth(),_delDt2.getUTCDate()) - Date.UTC(_now2.getUTCFullYear(),_now2.getUTCMonth(),_now2.getUTCDate())) / 86400000));
        const delCol = daysColorStyle(days);
        const icon = m.media_type==='Movie'?'🎬':'📺';
        const poster = m.poster_url
          ? `<img src="${m.poster_url}" style="width:100%;height:220px;object-fit:cover;border-radius:8px 8px 0 0" onerror="this.style.display='none'">`
          : `<div style="width:100%;height:220px;background:#ffffff08;border-radius:8px 8px 0 0;display:flex;align-items:center;justify-content:center;font-size:40px">${icon}</div>`;
        const titleEl = m.seerr_request_url
          ? `<a href="${escapeHtml(m.seerr_request_url)}" target="_blank" style="color:#e2e8f0;font-weight:600;font-size:12px;text-decoration:none;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escapeHtml(m.title)}">${escapeHtml(m.title)}</a>`
          : `<div style="color:#e2e8f0;font-weight:600;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escapeHtml(m.title)}">${escapeHtml(m.title)}</div>`;
        const req = m.seerr_username ? `<div style="font-size:10px;color:var(--muted);margin-top:2px">👤 ${escapeHtml(m.seerr_username)}</div>` : '';
        return `<div class="card" style="overflow:hidden;cursor:pointer;border:${selectedIds.has(m.id)?'1px solid var(--accent)':'1px solid var(--border)'}" onclick="toggleSelect(${m.id})" id="row-${m.id}">
          ${poster}
          <div style="padding:8px">
            ${titleEl}${req}
            <div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px">
              <span class="badge badge-${m.status}" style="font-size:10px">${m.status}</span>
              <span style="font-size:10px;${delCol};font-weight:600">${days<=0?'Imminent':days+'j'}</span>
            </div>
            ${m.status==='pending'?`<div style="display:flex;gap:4px;margin-top:6px">
              <button class="btn btn-ghost" style="flex:1;padding:3px;font-size:10px;justify-content:center" data-mid="${m.id}" data-title="${escapeHtml(m.title)}" data-type="${escapeHtml(m.media_type)}" data-poster="${escapeHtml(m.poster_url||'')}" data-lib="${escapeHtml(m.library_name||'')}" onclick="event.stopPropagation();openIgnoreModalFromEl(this)"><i class="fas fa-ban"></i></button>
              <button class="btn btn-danger" style="flex:1;padding:3px;font-size:10px;justify-content:center" onclick="event.stopPropagation();deleteNow(${m.id})"><i class="fas fa-trash"></i></button>
            </div>`:''}
          </div>
        </div>`;
      }).join('');
    } else {
      // List mode
      const grid = document.getElementById('queue-grid');
      if (grid) grid.style.display = 'none';
      document.getElementById('queue-table-wrap').style.display = 'block';
      tbody.innerHTML = data.items.map(m => {
        const added = m.added_date ? new Date(m.added_date).toLocaleDateString('fr-FR') : '—';
        const seen = m.last_played ? new Date(m.last_played).toLocaleDateString('fr-FR') : `<em style="color:#ef444480">Jamais</em>`;
        const del = new Date(m.delete_at).toLocaleDateString('fr-FR');
        const _delDt3 = new Date(m.delete_at); const _now3 = new Date();
        const days = Math.max(0, Math.round((Date.UTC(_delDt3.getUTCFullYear(),_delDt3.getUTCMonth(),_delDt3.getUTCDate()) - Date.UTC(_now3.getUTCFullYear(),_now3.getUTCMonth(),_now3.getUTCDate())) / 86400000));
        const delCol = daysColorStyle(days);
        const icon = m.media_type==='Movie'?'🎬':'📺';
        const lib = m.library_name || m.library_id;
        const poster = m.poster_url
          ? `<img src="${m.poster_url}" style="width:32px;height:48px;object-fit:cover;border-radius:3px;flex-shrink:0" onerror="this.style.display='none'">`
          : `<div style="width:32px;height:48px;background:#ffffff08;border-radius:3px;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:14px">${icon}</div>`;
        const titleLink = m.seerr_request_url
          ? `<a href="${escapeHtml(m.seerr_request_url)}" target="_blank" class="media-title-link">${escapeHtml(m.title)}</a>`
          : `<span style="color:#e2e8f0;font-weight:500">${escapeHtml(m.title)}</span>`;
        const reqCell = m.seerr_username
          ? `<div style="display:flex;align-items:center;gap:5px"><div style="width:20px;height:20px;border-radius:50%;background:#6366f120;display:flex;align-items:center;justify-content:center;flex-shrink:0"><i class="fas fa-user" style="font-size:9px;color:var(--accent2)"></i></div><span style="font-size:12px;color:#e2e8f0">${escapeHtml(m.seerr_username)}</span></div>`
          : `<span style="color:var(--muted);font-size:12px;font-style:italic">—</span>`;
        const badge = `<span class="badge badge-${m.status}">${m.status}</span>`;
        const actions = m.status==='pending'
          ? `<div style="display:flex;gap:3px">
              <button class="btn btn-ghost" style="padding:4px 7px;font-size:11px" data-mid="${m.id}" data-title="${escapeHtml(m.title)}" data-type="${escapeHtml(m.media_type)}" data-poster="${escapeHtml(m.poster_url||'')}" data-lib="${escapeHtml(m.library_name||'')}" onclick="openIgnoreModalFromEl(this)" title="Ignorer définitivement"><i class="fas fa-ban"></i></button>
              <button class="btn btn-danger" style="padding:4px 7px;font-size:11px" onclick="deleteNow(${m.id})" title="Supprimer maintenant"><i class="fas fa-trash"></i></button>
             </div>`
          : `<button class="btn btn-ghost" style="padding:4px 7px;font-size:11px" onclick="removeFromQueue(${m.id})"><i class="fas fa-times"></i></button>`;
        return `<tr onclick="toggleRow(event,${m.id})" style="cursor:pointer" id="row-${m.id}">
          <td onclick="event.stopPropagation()"><input type="checkbox" id="chk-${m.id}" onchange="toggleSelect(${m.id})" style="accent-color:var(--accent);width:14px;height:14px"></td>
          <td><div style="display:flex;align-items:center;gap:8px">${poster}<div>${titleLink}</div></div></td>
          <td style="color:var(--muted);font-size:12px">${lib}</td>
          <td>${reqCell}</td>
          <td style="color:var(--muted);font-size:12px">${added}</td>
          <td style="font-size:12px">${seen}</td>
          <td style="${delCol};font-weight:500;font-size:12px" title="${del}">${del}<br><span style="font-size:10px;opacity:.7">${days>0?days+'j':'aujourd\'hui'}</span></td>
          <td>${badge}</td>
          <td onclick="event.stopPropagation()">${actions}</td>
        </tr>`;
      }).join('');
      document.getElementById('chk-all').checked = false;
    }
  } catch(e) { toast('Erreur chargement file','error'); }
}

function toggleRow(e, id) {
  if (['BUTTON','I','A','INPUT'].includes(e.target.tagName)) return;
  toggleSelect(id);
}
function toggleSelect(id) {
  if (selectedIds.has(id)) selectedIds.delete(id);
  else selectedIds.add(id);
  const chk = document.getElementById(`chk-${id}`);
  if (chk) chk.checked = selectedIds.has(id);
  document.getElementById(`row-${id}`).style.background = selectedIds.has(id)?'#6366f115':'';
  updateBulkBar();
}
function toggleSelectAll() {
  const allChks = document.querySelectorAll('[id^="chk-"]:not(#chk-all)');
  const checked = document.getElementById('chk-all').checked;
  allChks.forEach(chk => {
    const id = parseInt(chk.id.replace('chk-',''));
    if (checked) { selectedIds.add(id); chk.checked=true; document.getElementById(`row-${id}`).style.background='#6366f115'; }
    else { selectedIds.delete(id); chk.checked=false; document.getElementById(`row-${id}`).style.background=''; }
  });
  updateBulkBar();
}
function updateBulkBar() {
  const bar = document.getElementById('bulk-bar');
  bar.style.display = selectedIds.size>0?'flex':'none';
  document.getElementById('bulk-count').textContent = `${selectedIds.size} sélectionné${selectedIds.size>1?'s':''}`;
}
async function bulkExclude() {
  try {
    await api('/api/media/bulk','POST',{ids:[...selectedIds],action:'ignore'});
    toast(`${selectedIds.size} exclu(s)`,'success'); selectedIds.clear(); loadQueue();
  } catch(e) { toast('Erreur lors de l\'exclusion','error'); }
}
async function bulkDelete() {
  try { await showConfirm({ title: 'Supprimer ' + selectedIds.size + ' média(s) ?', body: 'Ces médias seront supprimés de tous vos services (Emby, Radarr/Sonarr, Seerr, qBittorrent).', icon: 'trash', color: '#ef4444', okLabel: 'Supprimer' }); } catch(e) { return; }
  const r = await api('/api/media/bulk','POST',{ids:[...selectedIds],action:'delete'});
  toast(`${r.affected} supprimé(s)`,'success'); selectedIds.clear(); loadQueue();
}
function setPageSize(n) { queuePageSize=n; queueOffset=0; loadQueue(); }
function queuePage(dir) {
  const np = getCurrentPage()+dir;
  if (np<1||np>getPageCount()) return;
  queueOffset=(np-1)*queuePageSize; loadQueue();
}
async function excludeMedia(id) { await api(`/api/media/${id}/remove`,'DELETE'); toast('Exclu','success'); loadQueue(); }
async function deleteNow(id) {
  try { await showConfirm({ title: 'Supprimer maintenant ?', body: 'Ce média sera supprimé immédiatement de tous vos services.', icon: 'trash', color: '#ef4444', okLabel: 'Supprimer' }); } catch(e) { return; }
  try { await api(`/api/media/${id}/delete-now`,'POST'); toast('Supprimé','success'); loadQueue(); }
  catch(e) { toast('Erreur suppression','error'); }
}
async function removeFromQueue(id) { await api(`/api/media/${id}`,'DELETE'); loadQueue(); }
async function purgeDeleted() {
  try { await showConfirm({ title: 'Purger l\'historique ?', body: 'Toutes les entrées "Supprimé" seront retirées de la file d\'attente.', icon: 'broom', color: '#f59e0b', okLabel: 'Purger', okClass: 'btn-primary' }); } catch(e) { return; }
  const r = await api('/api/media/purge/deleted','DELETE');
  toast(r.purged !== undefined ? `${r.purged} entrée(s) purgée(s)` : 'Purgé', 'success');
  loadQueue(); loadDashboard();
}
async function regenPosters() {
  toast('Régénération des affiches...', 'info');
  try {
    await api('/api/media/regenerate-posters', 'POST');
    toast('Affiches en cours de régénération (quelques minutes)', 'success');
    setTimeout(() => loadQueue(), 10000);
  } catch(e) { toast('Erreur régénération', 'error'); }
}

async function enrichSeerr() {
  toast('Sync Seerr lancé...','info');
  await api('/api/media/enrich-seerr','POST');
  toast('Sync Seerr démarré — rechargez dans quelques secondes','success');
  setTimeout(()=>loadQueue(), 4000);
}

// ─── Seerr users cache (10 min TTL to avoid N requests per modal open) ──────
let _seerrUsersCache = null;
let _seerrUsersCacheTs = 0;
const _SEERR_USERS_TTL = 600000;
async function getSeerrUsers() {
  if (_seerrUsersCache && Date.now() - _seerrUsersCacheTs < _SEERR_USERS_TTL) {
    return _seerrUsersCache;
  }
  _seerrUsersCache = await api('/api/seerr-rules/users').catch(() => []);
  _seerrUsersCacheTs = Date.now();
  return _seerrUsersCache;
}

// ─── Libraries ────────────────────────────────────────────────────────────────
const FIELD_LABELS_FR = { days_since_added:'Ajouté depuis',days_not_watched:'Non vu depuis',play_count:'Nb lectures',never_watched:'Jamais regardé' };
const FIELD_LABELS_EN = { days_since_added:'Added since',days_not_watched:'Not watched since',play_count:'Play count',never_watched:'Never watched' };
const OP_LABELS = { gt:'>',gte:'≥',lt:'<',lte:'≤',eq:'=' };
function getFieldLabels() { return (typeof _lang !== 'undefined' && _lang === 'en') ? FIELD_LABELS_EN : FIELD_LABELS_FR; }
// Inline translation helper for template literals
function _(fr, en) { return (typeof _lang !== 'undefined' && _lang === 'en') ? en : fr; }
const FIELD_LABELS = new Proxy({}, { get(t,k) { return getFieldLabels()[k]; } });
let editingLibId = null, conditions = [], seerrConditions = [], availableSeerrUsers = [];

async function loadLibraries() {
  try {
    const libs = await api('/api/libraries/');
    const box = document.getElementById('libraries-list');
    if (!libs.length) {
      box.innerHTML = `<div class="card" style="padding:40px;text-align:center;color:var(--muted)"><i class="fas fa-layer-group" style="font-size:32px;margin-bottom:12px;display:block;color:var(--border)"></i>Aucune bibliothèque.</div>`;
      return;
    }
    box.innerHTML = libs.map(l => {
      const condText = (l.conditions||[]).map(c =>
        `<span style="background:#6366f120;border:1px solid #6366f140;border-radius:6px;padding:2px 8px;font-size:11px;color:var(--accent2)">${FIELD_LABELS[c.field]||c.field} ${OP_LABELS[c.op]||c.op} ${c.value}${['days_since_added','days_not_watched'].includes(c.field)?'j':''}</span>`
      ).join(`<span style="font-size:11px;color:var(--muted);padding:0 4px">${l.logic==='OR'?'OU':'ET'}</span>`);
      const seerrText = (l.seerr_conditions||[]).length
        ? `<span style="font-size:11px;color:#a78bfa;margin-left:6px"><i class="fas fa-user-check" style="margin-right:3px"></i>${l.seerr_conditions.length} filtre(s) Seerr</span>` : '';
      return `<div class="card" style="padding:18px;display:flex;align-items:center;justify-content:space-between;gap:16px">
        <div style="display:flex;align-items:center;gap:14px;flex:1;min-width:0">
          <div style="width:36px;height:36px;border-radius:10px;background:${l.enabled?'#6366f118':'#ffffff0a'};display:flex;align-items:center;justify-content:center;flex-shrink:0">
            <i class="fas fa-layer-group" style="color:${l.enabled?'var(--accent2)':'var(--muted)'}"></i>
          </div>
          <div style="min-width:0">
            <div style="font-weight:600;color:#e2e8f0;margin-bottom:4px">${escapeHtml(l.name)}</div>
            <div style="display:flex;flex-wrap:wrap;gap:4px;align-items:center">${condText||'<span style="font-size:11px;color:var(--muted);font-style:italic">Aucune condition</span>'}${seerrText}</div>
            <div style="font-size:11px;color:var(--muted);margin-top:4px">${_('Délai de grâce','Grace period')} : ${l.grace_days}${_('j','d')}${!l.enabled?(' · '+_('Désactivé','Disabled')):''}</div>
          </div>
        </div>
        <div style="display:flex;gap:6px;flex-shrink:0">
          <button class="btn btn-ghost" style="padding:6px 10px" title="${l.enabled?'Désactiver':'Activer'}" onclick="toggleLibrary('${l.id}',${!l.enabled})">
            <i class="fas fa-${l.enabled?'toggle-on':'toggle-off'}" style="color:${l.enabled?'#10b981':'#94a3b8'}"></i>
          </button>
          <button class="btn btn-ghost" style="padding:6px 10px" title="Scanner" onclick="triggerScanLibrary('${l.id}',this.dataset.n)" data-n="${l.name}"><i class="fas fa-magnifying-glass"></i></button>
          <button class="btn btn-ghost" style="padding:6px 10px" title="Cloner" onclick="cloneLibrary('${l.id}')"><i class="fas fa-clone"></i></button>
          <button class="btn btn-ghost" style="padding:6px 10px" title="Modifier" onclick="editLibrary('${l.id}')"><i class="fas fa-pen"></i></button>
          <button class="btn btn-ghost" style="padding:6px 10px;color:#ef4444" title="Supprimer" onclick="deleteLibrary('${l.id}')"><i class="fas fa-trash"></i></button>
        </div>
      </div>`;
    }).join('');
  } catch(e) { toast('Erreur bibliothèques','error'); }
}

function renderConditions() {
  const box = document.getElementById('conditions-container');
  if (!conditions.length) {
    box.innerHTML = '<div style="font-size:12px;color:var(--muted);text-align:center;padding:10px;background:#0a0c14;border-radius:8px;border:1px dashed var(--border)">Cliquez sur "Ajouter" pour créer une condition</div>';
    return;
  }
  box.innerHTML = conditions.map((c,i) => {
    const isNever = c.field==='never_watched';
    return `<div class="condition-row">
      <select class="select" style="flex:2" onchange="conditions[${i}].field=this.value;renderConditions()">
        <option value="days_since_added" ${c.field==='days_since_added'?'selected':''}>${typeof _lang!=='undefined'&&_lang==='en'?'Added since (days)':'Ajouté depuis (jours)'}</option>
        <option value="days_not_watched" ${c.field==='days_not_watched'?'selected':''}>${typeof _lang!=='undefined'&&_lang==='en'?'Not watched since (days)':'Non vu depuis (jours)'}</option>
        <option value="play_count" ${c.field==='play_count'?'selected':''}>${typeof _lang!=='undefined'&&_lang==='en'?'Play count':'Nombre de lectures'}</option>
        <option value="never_watched" ${c.field==='never_watched'?'selected':''}>${typeof _lang!=='undefined'&&_lang==='en'?'Never watched':'Jamais regardé'}</option>
      </select>
      ${isNever
        ? `<select class="select" style="flex:1" onchange="conditions[${i}].value=parseInt(this.value)"><option value="1" ${c.value===1?'selected':''}>= vrai</option><option value="0" ${c.value===0?'selected':''}>= faux</option></select><div style="flex:1"></div>`
        : `<select class="select" style="flex:1" onchange="conditions[${i}].op=this.value">
            <option value="gt" ${c.op==='gt'?'selected':''}>${typeof _lang!=='undefined'&&_lang==='en'?'> greater than':'> supérieur'}</option>
            <option value="gte" ${c.op==='gte'?'selected':''}>${typeof _lang!=='undefined'&&_lang==='en'?'≥ greater or equal':'≥ sup. ou égal'}</option>
            <option value="lt" ${c.op==='lt'?'selected':''}>${typeof _lang!=='undefined'&&_lang==='en'?'< less than':'< inférieur'}</option>
            <option value="lte" ${c.op==='lte'?'selected':''}>${typeof _lang!=='undefined'&&_lang==='en'?'≤ less or equal':'≤ inf. ou égal'}</option>
            <option value="eq" ${c.op==='eq'?'selected':''}">${typeof _lang!=='undefined'&&_lang==='en'?'= equal':'= égal'}</option>
           </select>
           <input class="input" type="number" style="flex:1;min-width:60px" value="${c.value}" min="0" onchange="conditions[${i}].value=parseInt(this.value)||0">`
      }
      <button class="btn btn-ghost" style="padding:5px 8px;color:#ef4444;flex-shrink:0" onclick="removeCondition(${i})"><i class="fas fa-times"></i></button>
    </div>`;
  }).join('');
}
function addCondition() { conditions.push({field:'days_since_added',op:'gt',value:30}); renderConditions(); }
function removeCondition(i) { conditions.splice(i,1); renderConditions(); }

function renderSeerrConditions() {
  const box = document.getElementById('seerr-conditions-container');
  if (!availableSeerrUsers.length) {
    box.innerHTML = '<div style="font-size:11px;color:var(--muted);padding:8px;background:#0a0c14;border-radius:6px">Configurez Seerr dans les paramètres pour activer le filtrage.</div>';
    return;
  }
  if (!seerrConditions.length) {
    box.innerHTML = '<div style="font-size:12px;color:var(--muted);text-align:center;padding:10px;background:#0a0c14;border-radius:8px;border:1px dashed var(--border)">Aucun filtre — tous les utilisateurs inclus</div>';
    return;
  }
  box.innerHTML = seerrConditions.map((c,i) => `
    <div class="condition-row">
      <select class="select" style="flex:1" onchange="seerrConditions[${i}].type=this.value">
        <option value="user_include" ${c.type==='user_include'?'selected':''}>${typeof _lang!=='undefined'&&_lang==='en'?'✅ Include only':'✅ Inclure uniquement'}</option>
        <option value="user_exclude" ${c.type==='user_exclude'?'selected':''}>${typeof _lang!=='undefined'&&_lang==='en'?'🚫 Exclude':'🚫 Exclure'}</option>
      </select>
      <select class="select" style="flex:2" onchange="seerrConditions[${i}].user_id=parseInt(this.value);seerrConditions[${i}].username=this.options[this.selectedIndex].text">
        ${availableSeerrUsers.map(u=>`<option value="${u.id}" ${u.id===c.user_id?'selected':''}>${u.username}</option>`).join('')}
      </select>
      <button class="btn btn-ghost" style="padding:5px 8px;color:#ef4444;flex-shrink:0" onclick="removeSeerrCondition(${i})"><i class="fas fa-times"></i></button>
    </div>`).join('');
}
function addSeerrCondition() {
  if (!availableSeerrUsers.length) { toast('Aucun utilisateur Seerr disponible','warn'); return; }
  seerrConditions.push({type:'user_include',user_id:availableSeerrUsers[0].id,username:availableSeerrUsers[0].username});
  renderSeerrConditions();
}
function removeSeerrCondition(i) { seerrConditions.splice(i,1); renderSeerrConditions(); }

async function openAddLibrary() {
  editingLibId=null; conditions=[{field:'days_since_added',op:'gt',value:30},{field:'days_not_watched',op:'gt',value:60}]; seerrConditions=[];
  document.getElementById('modal-title').textContent='Ajouter une bibliothèque';
  document.getElementById('lib-name').value=''; document.getElementById('lib-grace').value=7; document.getElementById('lib-logic').value='AND';
  renderConditions(); renderSeerrConditions();
  await loadEmbyLibOptions();
  availableSeerrUsers = await getSeerrUsers();
  renderSeerrConditions();
  document.getElementById('modal-library').style.display='flex';
}
async function editLibrary(id) {
  editingLibId=id;
  const libs = await api('/api/libraries/');
  const lib = libs.find(l=>l.id===id); if (!lib) return;
  conditions=JSON.parse(JSON.stringify(lib.conditions||[]));
  seerrConditions=JSON.parse(JSON.stringify(lib.seerr_conditions||[]));
  document.getElementById('modal-title').textContent='Modifier la bibliothèque';
  document.getElementById('lib-name').value=lib.name;
  document.getElementById('lib-grace').value=lib.grace_days??7;
  document.getElementById('lib-logic').value=lib.logic||'AND';
  renderConditions();
  await loadEmbyLibOptions(lib.emby_library_id);
  availableSeerrUsers = await getSeerrUsers();
  renderSeerrConditions();
  document.getElementById('modal-library').style.display='flex';
}
async function loadEmbyLibOptions(selected='') {
  const sel = document.getElementById('lib-emby-id');
  sel.innerHTML='<option value="">Chargement...</option>';
  try {
    const libs = await api('/api/libraries/emby');
    sel.innerHTML = libs.map(l=>`<option value="${l.id}">${l.name}${l.type?' ('+l.type+')':''}</option>`).join('');
    if (selected) { sel.value=selected; if (!sel.value) { const o=document.createElement('option'); o.value=selected; o.textContent=`(ID: ${selected})`; o.selected=true; sel.prepend(o); } }
  } catch(e) { sel.innerHTML='<option value="">Erreur Emby</option>'; }
}
async function saveLibrary() {
  const body = { name:document.getElementById('lib-name').value, emby_library_id:document.getElementById('lib-emby-id').value, grace_days:parseInt(document.getElementById('lib-grace').value)||7, logic:document.getElementById('lib-logic').value, conditions, seerr_conditions:seerrConditions, enabled:true };
  if (!body.name||!body.emby_library_id) { toast('Remplissez tous les champs','warn'); return; }
  try {
    const savedId = editingLibId;
    if (editingLibId) await api(`/api/libraries/${editingLibId}`,'PUT',body);
    else await api('/api/libraries/','POST',body);
    toast(editingLibId?'Mise à jour':'Bibliothèque ajoutée','success');
    closeModal(); loadLibraries();
    if (savedId) { const r=await api(`/api/libraries/${savedId}/reevaluate`,'POST'); if (r.removed>0) toast(`${r.removed} média(s) retiré(s) de la file`,'info'); }
  } catch(e) { toast('Erreur sauvegarde','error'); }
}
async function deleteLibrary(id) { try { await showConfirm({ title: 'Supprimer cette bibliothèque ?', body: 'La bibliothèque et ses règles seront supprimées. Les médias déjà en file d\'attente ne seront pas affectés.', icon: 'layer-group', color: '#ef4444', okLabel: 'Supprimer' }); } catch(e) { return; } await api(`/api/libraries/${id}`,'DELETE'); toast('Supprimée','success'); loadLibraries(); }
async function cloneLibrary(id) {
  try {
    await api(`/api/libraries/${id}/clone`, 'POST');
    toast('Bibliothèque clonée', 'success');
    await loadLibraries();
  } catch(e) { toast('Erreur clonage : ' + e.message, 'error'); }
}
async function toggleLibrary(id, newEnabled) {
  await api(`/api/libraries/${id}`,'PUT',{enabled: newEnabled});
  toast(newEnabled ? 'Bibliothèque activée' : 'Bibliothèque désactivée', 'success');
  loadLibraries();
}

function openIgnoreModalFromEl(el) {
  openIgnoreModal(
    parseInt(el.dataset.mid),
    el.dataset.title,
    el.dataset.type,
    el.dataset.poster,
    el.dataset.lib
  );
}

function closeModal() { document.getElementById('modal-library').style.display='none'; editingLibId=null; conditions=[]; seerrConditions=[]; }


// ─── Settings field lists ─────────────────────────────────────────────────────
const SETTINGS_FORM_FIELDS = [
  'emby_url','emby_api_key','emby_external_url',
  'radarr_url','radarr_api_key',
  'sonarr_url','sonarr_api_key',
  'seerr_url','seerr_api_key','seerr_external_url',
  'qbit_url','qbit_proxy_url','qbit_user','qbit_password',
  'emby_leaving_soon_collection','emby_leaving_soon_days','qbit_tag',
  'discord_webhook','discord_notif_thresholds',
];


// ─── Media Servers ────────────────────────────────────────────────────────────
let _mediaServers = [];

async function loadMediaServers() {
  try {
    _mediaServers = await api('/api/settings/media-servers');
    renderMediaServers();
    updateMediaServerIconFromServers();
    // Show collection section if any enabled Emby server
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
    const EMBY_URL = 'https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/emby.png';
    const JF_URL = 'https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/jellyfin.png';
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
    // Show/hide collection section
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
      // Update local state
      const s = _mediaServers.find(x => String(x.id) === String(id));
      if (s) s.type = r.server_type;
      renderMediaServers();
      updateMediaServerIconFromServers();
      // Show/hide collection section
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
let _scanIntervalMin=360, _delIntervalMin=60;
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
      updateMediaServerIcon('mixed');  // multiple different types → split icon
    } else {
      updateMediaServerIcon('');  // no enabled servers → generic
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
  const EMBY_ICON = 'https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/emby.png';
  const JF_ICON = 'https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/jellyfin.png';
  const EMBY_URL = EMBY_ICON; const JF_URL = JF_ICON;  // aliases used in template literals
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
    if (embyOnly) embyOnly.style.display = 'block';  // Collections/overlay work with Jellyfin too!
    if (nonEmbyNotice) nonEmbyNotice.style.display = 'none';
  } else if (type === 'unknown') {
    if (iconWrap) iconWrap.innerHTML = GENERIC;
    if (headerLogo) headerLogo.innerHTML = GENERIC_HEADER;
    if (pill) pill.style.display = 'none';
    if (detected) { detected.textContent = 'Serveur non reconnu — fonctionnalités Collection/Overlay disponibles avec Emby uniquement'; detected.style.color = 'var(--muted)'; detected.style.fontStyle = 'italic'; }
    if (embyOnly) embyOnly.style.display = 'none';
    if (nonEmbyNotice) { nonEmbyNotice.style.display = 'block'; if (nonEmbyMsg) nonEmbyMsg.innerHTML = '⚠️ <strong>Serveur non reconnu</strong> — Les fonctionnalités Collection et Overlay d\'affiches sont disponibles uniquement avec Emby.'; nonEmbyNotice.style.color = '#94a3b8'; }
  } else if (type === 'mixed') {
    // Emby + Jellyfin — option D : vertical split
    const SPLIT_TAB = `<div style="width:18px;height:18px;border-radius:4px;position:relative;overflow:hidden;flex-shrink:0"><img src="${EMBY_ICON}" style="position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;clip-path:inset(0 50% 0 0)"><img src="${JF_ICON}" style="position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;clip-path:inset(0 0 0 50%)"></div>`;
    const SPLIT_HDR = `<div style="width:44px;height:44px;border-radius:10px;position:relative;overflow:hidden"><img src="${EMBY_ICON}" style="position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;clip-path:inset(0 50% 0 0)"><img src="${JF_ICON}" style="position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;clip-path:inset(0 0 0 50%)"></div>`;
    if (iconWrap) iconWrap.innerHTML = SPLIT_TAB;
    if (headerLogo) headerLogo.innerHTML = SPLIT_HDR;
    if (pill) { pill.textContent = 'Multi'; pill.style.cssText = 'display:inline;font-size:9px;padding:1px 5px;border-radius:10px;font-weight:600;background:#6366f125;color:#818cf8'; }
    if (detected) { detected.textContent = 'Emby + Jellyfin — plusieurs serveurs actifs'; detected.style.color = '#818cf8'; detected.style.fontStyle = 'normal'; }
    if (embyOnly) embyOnly.style.display = 'block'; // Show collection (at least one Emby)
    if (nonEmbyNotice) nonEmbyNotice.style.display = 'none';
  } else {
    // '' — not tested
    if (iconWrap) iconWrap.innerHTML = GENERIC;
    if (headerLogo) headerLogo.innerHTML = GENERIC_HEADER;
    if (pill) pill.style.display = 'none';
    if (detected) { detected.textContent = 'Non encore testé — cliquez Tester pour détecter'; detected.style.color = 'var(--muted)'; detected.style.fontStyle = 'italic'; }
    if (embyOnly) embyOnly.style.display = 'block';
    if (nonEmbyNotice) nonEmbyNotice.style.display = 'none';
  }
}


async function loadSettings(force=false) {
  if (_settingsLoaded && _settingsDirty && !force) return;
  try {
    const s = await api('/api/settings/');
    SETTINGS_FORM_FIELDS.forEach(f => {
      const el=document.getElementById(f); if(el) el.value=s[f]||'';
    });
    document.getElementById('dry-run-toggle').checked = s.dry_run==='true';
    // Sync sidebar toggle
    const _sdr = document.getElementById('sidebar-dry-run');
    if (_sdr) _sdr.checked = s.dry_run==='true';
    _updateSidebarDryRunStyle(s.dry_run==='true');
    // Intervals — convert minutes → value + unit selector
    const scanMin = parseInt(s.scan_interval_minutes || '360');
    _scanIntervalMin = scanMin;  // cache for progress bars
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
    // Media server icon — read all servers to handle multi-server correctly
    updateMediaServerIconFromServers();
    // Restore active tab
    switchSettingsTab(_activeSettingsTab);
    if (_activeSettingsTab === 'media') loadMediaServers();
    if(document.getElementById('deleted_retention_days')) document.getElementById('deleted_retention_days').value = s.deleted_retention_days||'90';
    const qa = document.getElementById('qbit_action'); if(qa) qa.value = s.qbit_action||'tag_only';
    const ov = document.getElementById('emby_leaving_soon_overlay'); if(ov) ov.checked = s.emby_leaving_soon_overlay==='true';
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
  };
  // Intervals — convert to minutes before saving
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
  // Save current form values first so the test backend reads fresh settings from DB
  try { await api('/api/settings/','POST',collectFormValues()); _settingsDirty=false; } catch(e) {}
  try { const r=await api(`/api/libraries/test/${service}`,'POST'); toast(r.message||'OK',r.ok?'success':'error'); }
  catch(e) { toast('Erreur connexion','error'); }
}

// ─── Logs ─────────────────────────────────────────────────────────────────────
async function loadLogs() {
  const level=document.getElementById('log-level-filter')?.value||'';
  const cat=document.getElementById('log-cat-filter')?.value||'';
  try {
    const logs=await api(`/api/logs/?limit=300${level?'&level='+level:''}${cat?'&source='+cat:''}`);
    const box=document.getElementById('logs-container');
    if (!logs.length) { box.innerHTML='<div style="color:var(--muted)">Aucun log</div>'; return; }
    box.innerHTML=logs.map(l=>{
      const ts=l.ts?new Date(l.ts).toLocaleString('fr-FR',{dateStyle:'short',timeStyle:'medium'}):'—';
      return `<div class="log-row log-${escapeHtml(l.level||'')}"><span class="log-ts">${ts}</span><span class="log-level">${escapeHtml(l.level||'')}</span><span class="log-cat">${escapeHtml(l.source||'')}</span><span style="color:var(--text)">${escapeHtml(l.message||'')}</span></div>`;
    }).join('');
  } catch(e) { toast('Erreur logs','error'); }
}
async function clearLogs() { await api('/api/logs/','DELETE'); toast('Logs vidés','success'); loadLogs(); }

// ─── Jobs ─────────────────────────────────────────────────────────────────────
async function loadJobs() {
  try {
    const [history,sched]=await Promise.all([api('/api/jobs/history'),api('/api/scheduler/status')]);
    // Only show scan_job and deletion_job — internal jobs are hidden
    const visibleJobs = sched.filter(j => j.id==='scan_job' || j.id==='deletion_job');
    document.getElementById('scheduler-cards').innerHTML=visibleJobs.map(j=>{
      const nxt=j.next_run?new Date(j.next_run):null;
      const diff=nxt?Math.round((nxt-Date.now())/60000):null;
      const label=j.id==='scan_job'?'Scan bibliothèques':'Vérification suppressions';
      const icon=j.id==='scan_job'?'magnifying-glass':'trash-can';
      return `<div class="card" style="padding:16px;display:flex;align-items:center;gap:14px">
        <div style="width:40px;height:40px;border-radius:10px;background:#6366f118;display:flex;align-items:center;justify-content:center;flex-shrink:0">
          <i class="fas fa-${icon}" style="color:var(--accent2)"></i>
        </div>
        <div style="flex:1">
          <div style="font-weight:600;color:#e2e8f0;font-size:13px">${label}</div>
          <div style="font-size:11px;color:var(--muted);margin-top:2px">${nxt ? `${nxt.toLocaleString('fr-FR', {day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})} — ${_fmtCountdown(diff)}` : 'Non planifié'}</div>
        </div>
        <button class="btn btn-ghost" style="padding:6px 10px;font-size:12px" onclick="${j.id==='scan_job'?'triggerScan':'triggerDeletion'}()"><i class="fas fa-play"></i>Lancer</button>
      </div>`;
    }).join('');
    const tbody=document.getElementById('jobs-tbody');
    if (!history.length) { tbody.innerHTML='<tr><td colspan="6" style="text-align:center;padding:40px;color:var(--muted)">Aucun historique</td></tr>'; return; }
    // Deduplicate: keep only the most recent entry per (job_type, minute)
    const seen=new Set();
    const deduped=history.filter(j=>{
      const key=`${j.job_name||j.job_type}|${(j.started_at||'').slice(0,16)}`;
      if(seen.has(key)) return false; seen.add(key); return true;
    });
    tbody.innerHTML=deduped.map(j=>{
      const start=j.started_at?new Date(j.started_at).toLocaleString('fr-FR',{dateStyle:'short',timeStyle:'medium'}):'—';
      const end=j.finished_at?new Date(j.finished_at).toLocaleString('fr-FR',{dateStyle:'short',timeStyle:'medium'}):'—';
      const dur=j.finished_at?Math.round((new Date(j.finished_at)-new Date(j.started_at))/1000)+'s':'…';
      const dc=j.status==='success'?'success':j.status==='error'?'error':j.status==='interrupted'?'warning':'running';
      const jn=j.job_name||j.job_type||'';
      const lbl=jn==='scan'||jn==='scan_library'?'<i class="fas fa-magnifying-glass" style="color:var(--muted)"></i> Scan':'<i class="fas fa-trash-can" style="color:var(--muted)"></i> Vérification';
      return `<tr><td style="color:#e2e8f0">${lbl}</td><td style="color:var(--muted)">${start}</td><td style="color:var(--muted)">${end}</td><td style="color:var(--muted)">${dur}</td><td><span class="badge badge-${dc}">${j.status}</span></td><td style="color:var(--muted);font-size:12px">${j.result||'—'}</td></tr>`;
    }).join('');
  } catch(e) { toast('Erreur jobs','error'); }
}

// ─── Actions ──────────────────────────────────────────────────────────────────
async function triggerScan() {
  try { const st=await api('/api/media/job-status'); if(st.scan_running){toast('Un scan est déjà en cours','warn');return;} } catch(e){}
  await api('/api/scan/trigger','POST'); toast('Scan démarré','info');
  setTimeout(()=>{ if(currentPage==='jobs') loadJobs(); },800);
}
async function triggerDeletion() {
  try { const st=await api('/api/media/job-status'); if(st.deletion_running){toast('Une vérification est déjà en cours','warn');return;} } catch(e){}
  await api('/api/deletion/trigger','POST'); toast('Vérification lancée','info');
  setTimeout(()=>{ if(currentPage==='jobs') loadJobs(); },800);
}
async function triggerScanLibrary(libId, libName) {
  try { const st=await api('/api/media/job-status'); if(st.scan_running){toast('Un scan est déjà en cours','warn');return;} } catch(e){}
  await api(`/api/scan/library/${libId}`,'POST');
  toast(`Scan de "${libName}" démarré`,'info');
  setTimeout(()=>{ if(currentPage==='jobs') loadJobs(); },800);
}
function logout() { clearToken(); showLoginScreen(); }

async function syncEmbyCollection() {
  toast('Synchronisation collection Emby...', 'info');
  try {
    await api('/api/emby-collection/sync', 'POST');
    toast('Collection Emby synchronisée', 'success');
  } catch(e) { toast('Erreur sync collection', 'error'); }
}

// ─── Auto-refresh ─────────────────────────────────────────────────────────────
setInterval(()=>{
  if (document.visibilityState !== 'visible') return;
  if(currentPage==='logs') loadLogs();
  if(currentPage==='jobs') loadJobs();
  loadSchedulerInfo();  // sidebar bars always visible
},15000);

// ─── Ignored ──────────────────────────────────────────────────────────────────
let _ignoredSearchTimer = null;
function debounceIgnoredSearch() {
  clearTimeout(_ignoredSearchTimer);
  _ignoredSearchTimer = setTimeout(loadIgnored, 350);
}

async function loadIgnored() {
  const box = document.getElementById('ignored-list');
  const search = document.getElementById('ignored-search')?.value?.trim() || '';
  try {
    const items = await api(`/api/ignored/${search ? '?search='+encodeURIComponent(search) : ''}`);
    if (!items.length) {
      box.innerHTML = `<div class="card" style="padding:40px;text-align:center;color:var(--muted)">
        <i class="fas fa-ban" style="font-size:32px;display:block;margin-bottom:12px;color:var(--border)"></i>
        Aucun média ignoré.<br><span style="font-size:12px">Utilisez le bouton <i class="fas fa-ban"></i> dans la file d'attente pour ignorer définitivement un média.</span>
      </div>`;
      return;
    }
    box.innerHTML = items.map(item => {
      const poster = item.poster_url
        ? `<img src="${item.poster_url}" style="width:48px;height:72px;object-fit:cover;border-radius:6px;flex-shrink:0" onerror="this.style.display='none'">`
        : `<div style="width:48px;height:72px;background:#ffffff08;border-radius:6px;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:20px">${item.media_type==='Movie'?'🎬':'📺'}</div>`;
      const lib = item.library_name || item.library_id || '?';
      const dt = item.ignored_at ? new Date(item.ignored_at).toLocaleDateString('fr-FR',{day:'numeric',month:'short',year:'numeric'}) : '?';
      let expireBadge = '';
      if (item.expire_at) {
        const expDt = new Date(item.expire_at);
        const daysLeft = Math.ceil((expDt - Date.now()) / 86400000);
        const expStr = expDt.toLocaleDateString('fr-FR',{day:'numeric',month:'short',year:'numeric'});
        const col = daysLeft <= 7 ? '#ef4444' : daysLeft <= 30 ? '#f59e0b' : '#10b981';
        expireBadge = `<div style="font-size:12px;color:${col};margin-top:3px"><i class="fas fa-clock" style="font-size:10px;margin-right:4px"></i>${_('Expire le','Expires on')} ${expStr} (${daysLeft > 0 ? _('dans','in')+' '+daysLeft+'j' : _("aujourd'hui",'today')})</div>`;
      } else {
        expireBadge = `<div style="font-size:12px;color:var(--muted);margin-top:3px"><i class="fas fa-infinity" style="font-size:10px;margin-right:4px"></i>${_('Ignoré définitivement','Permanently ignored')}</div>`;
      }
      return `<div class="card" style="padding:14px;display:flex;align-items:center;gap:14px">
        ${poster}
        <div style="flex:1;min-width:0">
          <div style="font-weight:600;color:#e2e8f0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(item.title)}</div>
          <div style="font-size:12px;color:var(--muted);margin-top:3px">📚 ${lib} · ${_('Ignoré le','Ignored on')} ${dt}</div>
          ${expireBadge}
          ${item.reason ? `<div style="font-size:12px;color:#f59e0b;margin-top:3px;display:flex;align-items:center;gap:4px"><i class="fas fa-comment-dots" style="font-size:10px"></i>${escapeHtml(item.reason)}</div>` : ''}
        </div>
        <button class="btn btn-ghost" style="padding:7px 12px;flex-shrink:0;color:#10b981" data-id="${item.id}" data-title="${escapeHtml(item.title)}" onclick="unignoreMediaFromEl(this)">
          <i class="fas fa-rotate-left"></i>Remettre
        </button>
      </div>`;
    }).join('');
  } catch(e) { toast('Erreur chargement ignorés','error'); }
}

function unignoreMediaFromEl(el) {
  unignoreMedia(parseInt(el.dataset.id), el.dataset.title);
}
async function unignoreMedia(id, title) {
  try { await showConfirm({ title: 'Remettre en file d\'attente ?', body: escapeHtml(title), icon: 'rotate-left', color: '#10b981', okLabel: 'Remettre', okClass: 'btn-primary' }); } catch(e) { return; }
  try {
    await api(`/api/ignored/${id}/requeue`, 'POST');
    toast(`"${title}" remis en file d'attente`, 'success');
  } catch(e) {
    // Fallback: simple delete (item will re-appear at next scan)
    await api(`/api/ignored/${id}`, 'DELETE');
    toast(`"${title}" retiré des ignorés — sera détecté au prochain scan`, 'info');
  }
  loadIgnored();
  loadDashboard();
}

// ─── Ignore modal ─────────────────────────────────────────────────────────────
let _ignoreMediaId = null;

function openIgnoreModal(id, title, mediaType, posterUrl, libraryName) {
  _ignoreMediaId = id;
  document.getElementById('ignore-reason').value = '';
  const icon = mediaType === 'Movie' ? '🎬' : '📺';
  const poster = posterUrl
    ? `<img src="${posterUrl}" style="width:40px;height:60px;object-fit:cover;border-radius:4px;flex-shrink:0" onerror="this.style.display='none'">`
    : `<div style="width:40px;height:60px;background:#ffffff08;border-radius:4px;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:18px">${icon}</div>`;
  document.getElementById('ignore-media-info').innerHTML = `
    ${poster}
    <div>
      <div style="font-weight:600;color:#e2e8f0">${escapeHtml(title)}</div>
      <div style="font-size:12px;color:var(--muted);margin-top:3px">📚 ${escapeHtml(libraryName)}</div>
    </div>`;
  document.getElementById('modal-ignore').style.display = 'flex';
  setTimeout(() => document.getElementById('ignore-reason').focus(), 100);
}

function closeIgnoreModal() {
  document.getElementById('modal-ignore').style.display = 'none';
  _ignoreMediaId = null;
}

async function confirmIgnore() {
  if (!_ignoreMediaId) return;
  const reason = document.getElementById('ignore-reason').value.trim();
  const expireDays = parseInt(document.getElementById('ignore-expire')?.value || '0') || 0;
  try {
    let url = `/api/media/${_ignoreMediaId}/ignore?reason=${encodeURIComponent(reason)}`;
    if (expireDays > 0) url += `&expire_days=${expireDays}`;
    await api(url, 'POST');
    toast(expireDays > 0 ? `Ignoré pour ${expireDays} jours` : 'Ignoré définitivement', 'success');
    closeIgnoreModal();
    loadQueue();
    loadDashboard();
  } catch(e) { toast('Erreur','error'); }
}

// Enter key in ignore modal
document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && document.getElementById('modal-ignore').style.display !== 'none') {
    confirmIgnore();
  }
});

// ─── Discord Mappings ─────────────────────────────────────────────────────────
let _discordMappings = {}; // seerr_user_id -> discord_id

async function loadDiscordMappings() {
  const box = document.getElementById('discord-mappings-list');
  if (!box) return;
  box.innerHTML = '<div style="font-size:12px;color:var(--muted)">Chargement...</div>';

  try {
    // Get Seerr users with their known discord IDs from Seerr
    const users = await api('/api/seerr-rules/users');
    // Get saved mappings from DB
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
  // Debounce saves
  clearTimeout(_discordSaveTimers[userId]);
  _discordSaveTimers[userId] = setTimeout(async () => {
    try {
      await api('/api/seerr-rules/discord-mappings', 'POST', {
        seerr_user_id: userId,
        seerr_username: username,
        discord_id: discordId.trim()
      });
      // Small visual feedback
      const input = document.getElementById(`discord-map-${userId}`);
      if (input) {
        input.style.borderColor = '#10b981';
        setTimeout(() => { input.style.borderColor = ''; }, 1500);
      }
    } catch(e) { /* silent */ }
  }, 800);
}

// ─── WebSocket ────────────────────────────────────────────────────────────────
let _ws = null;
let _wsReconnectTimer = null;

function initWebSocket() {
  if (_ws && (_ws.readyState === WebSocket.OPEN || _ws.readyState === WebSocket.CONNECTING)) return;
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  _ws = new WebSocket(`${proto}//${location.host}/ws`);

  _ws.onopen = () => {
    // Authenticate
    _ws.send(JSON.stringify({ token: _token }));
  };

  _ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === 'ping') { _ws.send('ping'); return; }
    if (msg.type === 'connected') {
      document.getElementById('ws-indicator').style.background = '#10b981';
      document.getElementById('ws-indicator').title = 'Temps réel connecté';
      return;
    }
    if (msg.type === 'log') {
      // Prepend to log container if on logs page
      if (currentPage === 'logs') _prependLog(msg.data);
      // Refresh stats on dashboard
      if (currentPage === 'dashboard') loadDashboard();
    }
    if (msg.type === 'stats') {
      // Could refresh queue count etc.
    }
  };

  _ws.onclose = (event) => {
    document.getElementById('ws-indicator').style.background = '#ef4444';
    document.getElementById('ws-indicator').title = 'Temps réel déconnecté';
    // 1008 = Policy Violation (auth rejected) — don't retry, session likely expired
    if (event.code === 1008) {
      document.getElementById('ws-indicator').title = 'Session expirée — reconnectez-vous';
      return;
    }
    clearTimeout(_wsReconnectTimer);
    _wsReconnectTimer = setTimeout(initWebSocket, 3000);
  };

  _ws.onerror = () => {
    _ws.close();
  };
}

function _prependLog(log) {
  const box = document.getElementById('logs-container');
  if (!box) return;
  const ts = log.ts?new Date(log.ts).toLocaleString('fr-FR',{dateStyle:'short',timeStyle:'medium'}):'—';
  const row = document.createElement('div');
  // escapeHtml for class to prevent class injection
  row.className = `log-row log-${escapeHtml(log.level||'')}`;
  row.style.background = '#6366f118';
  const tsSpan = document.createElement('span');
  tsSpan.className = 'log-ts'; tsSpan.textContent = ts;
  const levelSpan = document.createElement('span');
  levelSpan.className = 'log-level'; levelSpan.textContent = log.level||'';
  const catSpan = document.createElement('span');
  catSpan.className = 'log-cat'; catSpan.textContent = log.source||'';
  const msgSpan = document.createElement('span');
  msgSpan.style.color = 'var(--text)'; msgSpan.textContent = log.message||'';
  row.append(tsSpan, levelSpan, catSpan, msgSpan);
  box.prepend(row);
  setTimeout(() => { row.style.background = ''; row.style.transition = 'background 1s'; }, 100);
  const rows = box.querySelectorAll('.log-row');
  if (rows.length > 300) rows[rows.length-1].remove();
}

initAuth();

// ─── Calendar ─────────────────────────────────────────────────────────────────
let _calEvents = {};
let _calYear = new Date().getFullYear();
let _calMonth = new Date().getMonth(); // 0-indexed
let _calSelected = null;

async function loadCalendar() {
  try {
    const data = await api('/api/calendar');
    _calEvents = data.events || {};
    renderCalendar();
  } catch(e) { toast('Erreur calendrier','error'); }
}

function calPrevMonth() { _calMonth--; if(_calMonth<0){_calMonth=11;_calYear--;} renderCalendar(); }
function calNextMonth() { _calMonth++; if(_calMonth>11){_calMonth=0;_calYear++;} renderCalendar(); }
function calGoToday() { const n=new Date(); _calYear=n.getFullYear(); _calMonth=n.getMonth(); _calSelected=null; renderCalendar(); }

function renderCalendar() {
  const months = ['Janvier','Février','Mars','Avril','Mai','Juin','Juillet','Août','Septembre','Octobre','Novembre','Décembre'];
  document.getElementById('cal-title').textContent = (() => {
    const mn = new Date(_calYear, _calMonth, 1).toLocaleString(_lang === 'en' ? 'en-US' : 'fr-FR', {month:'long'});
    return mn.charAt(0).toUpperCase() + mn.slice(1) + ' ' + _calYear;
  })();

  const firstDay = new Date(_calYear, _calMonth, 1);
  const daysInMonth = new Date(_calYear, _calMonth+1, 0).getDate();
  // Monday-first: 0=Mon ... 6=Sun
  let startDow = (firstDay.getDay()+6)%7;
  const today = new Date().toISOString().slice(0,10);

  let cells = '';
  // Empty cells before first day
  for(let i=0;i<startDow;i++) cells += `<div style="min-height:70px"></div>`;
  // Day cells
  for(let d=1;d<=daysInMonth;d++) {
    const dateStr = `${_calYear}-${String(_calMonth+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const events = _calEvents[dateStr] || [];
    const isToday = dateStr===today;
    const isSelected = dateStr===_calSelected;
    const hasEvents = events.length>0;
    cells += `<div onclick="selectCalDay('${dateStr}')" style="
      min-height:70px;padding:6px;border-radius:8px;cursor:pointer;
      background:${isSelected?'#6366f130':hasEvents?'#f59e0b08':'#ffffff05'};
      border:1px solid ${isSelected?'var(--accent)':isToday?'#6366f160':hasEvents?'#f59e0b30':'var(--border)'};
      transition:all .15s
    ">
      <div style="font-size:13px;font-weight:${isToday?'700':'500'};color:${isToday?'var(--accent2)':'var(--text)'};margin-bottom:4px">${d}</div>
      ${events.length ? `<div style="display:flex;flex-direction:column;gap:2px">
        ${events.slice(0,3).map(e=>`<div style="font-size:10px;background:#f59e0b20;color:#f59e0b;border-radius:3px;padding:1px 4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${e.media_type==='Movie'?'🎬':'📺'} ${e.title}</div>`).join('')}
        ${events.length>3?`<div style="font-size:10px;color:var(--muted)">+${events.length-3} autre(s)</div>`:''}
      </div>` : ''}
    </div>`;
  }

  document.getElementById('cal-grid').innerHTML = cells;

  if (_calSelected) renderCalDetail(_calSelected);
}

function selectCalDay(dateStr) {
  _calSelected = _calSelected===dateStr ? null : dateStr;
  renderCalendar();
}

function renderCalDetail(dateStr) {
  const events = _calEvents[dateStr] || [];
  const box = document.getElementById('cal-detail');
  if (!events.length) { box.innerHTML=''; return; }
  const d = new Date(dateStr+'T12:00:00');
  const label = d.toLocaleDateString('fr-FR',{weekday:'long',day:'numeric',month:'long',year:'numeric'});
  box.innerHTML = `
    <div class="card" style="padding:20px">
      <div style="font-weight:600;color:#e2e8f0;margin-bottom:14px;display:flex;align-items:center;gap:8px">
        <i class="fas fa-calendar-day" style="color:#f59e0b"></i>${label} — ${events.length} suppression(s)
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px">
        ${events.map(e => {
          const poster = e.poster_url ? `<img src="${e.poster_url}" style="width:50px;height:75px;object-fit:cover;border-radius:4px;flex-shrink:0" onerror="this.style.display='none'">` : `<div style="width:50px;height:75px;background:#ffffff08;border-radius:4px;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:18px">${e.media_type==='Movie'?'🎬':'📺'}</div>`;
          const title = e.seerr_request_url
            ? `<a href="${escapeHtml(e.seerr_request_url)}" target="_blank" style="color:#e2e8f0;font-weight:500;font-size:12px;text-decoration:none">${escapeHtml(e.title)}</a>`
            : `<span style="color:#e2e8f0;font-weight:500;font-size:12px">${escapeHtml(e.title)}</span>`;
          const lib = escapeHtml(e.library_name || '');
          const req = e.seerr_username ? `<div style="font-size:11px;color:var(--muted)">👤 ${escapeHtml(e.seerr_username)}</div>` : '';
          return `<div style="display:flex;gap:10px;padding:10px;background:#ffffff05;border-radius:8px;border:1px solid var(--border)">
            ${poster}
            <div style="min-width:0;flex:1">
              ${title}
              <div style="font-size:11px;color:var(--muted);margin-top:3px">📚 ${lib}</div>
              ${req}
            </div>
          </div>`;
        }).join('')}
      </div>
    </div>`;
}

// ─── Storage ──────────────────────────────────────────────────────────────────
function fmtSize(bytes) {
  if (!bytes) return '0 B';
  const units = ['B','KB','MB','GB','TB'];
  let i=0; let v=bytes;
  while(v>=1024&&i<units.length-1){v/=1024;i++;}
  return `${v.toFixed(i>1?2:0)} ${units[i]}`;
}

async function loadStorage() {
  const box = document.getElementById('storage-content');
  box.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)"><i class="fas fa-spinner fa-spin" style="font-size:24px;margin-bottom:12px;display:block"></i>Chargement...</div>';
  try {
    const data = await api('/api/storage/');
    let html = '';

    // ── Disk usage ─────────────────────────────────────────────────────────
    const disks = data.disks || [];
    if (disks.length) {
      html += `<div class="card" style="padding:20px">
        <div style="font-weight:600;margin-bottom:16px;display:flex;align-items:center;gap:8px">
          <i class="fas fa-hard-drive" style="color:var(--accent2)"></i>${_('Utilisation disque','Disk usage')}
        </div>
        <div style="display:flex;flex-direction:column;gap:16px">
          ${disks.map(f => {
            const used = f.total - f.free;
            const pct = f.total > 0 ? Math.round(used / f.total * 100) : 0;
            const col = pct > 90 ? '#ef4444' : pct > 75 ? '#f59e0b' : '#10b981';
            const accessible = f.accessible !== false;
            return `<div>
              <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
                <div style="min-width:0;flex:1">
                  <div style="font-size:13px;color:#e2e8f0;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${f.path || f.label || '?'}</div>
                  <div style="font-size:11px;color:var(--muted);margin-top:2px">${f.source}${!accessible?' · <span style="color:#ef4444">Non accessible</span>':''}</div>
                </div>
                <div style="text-align:right;flex-shrink:0;margin-left:16px">
                  <div style="font-size:18px;font-weight:700;color:${col}">${pct}%</div>
                  <div style="font-size:11px;color:var(--muted)">${fmtSize(used)} / ${fmtSize(f.total)}</div>
                </div>
              </div>
              <div style="height:10px;background:#ffffff10;border-radius:5px;overflow:hidden">
                <div style="height:100%;width:${pct}%;background:linear-gradient(90deg,${col}aa,${col});border-radius:5px;transition:width .6s ease"></div>
              </div>
              <div style="display:flex;justify-content:space-between;margin-top:5px">
                <span style="font-size:11px;color:var(--muted)">${fmtSize(f.free)} libres</span>
                <span style="font-size:11px;color:var(--muted)">${fmtSize(f.total)} total</span>
              </div>
            </div>`;
          }).join('<div style="border-top:1px solid var(--border)"></div>')}
        </div>
      </div>`;
    } else {
      html += `<div class="card" style="padding:20px;text-align:center;color:var(--muted)">
        <i class="fas fa-hard-drive" style="font-size:24px;margin-bottom:8px;display:block"></i>
        Données disque non disponibles — vérifiez la configuration Radarr/Sonarr
      </div>`;
    }

    // ── Media stats grid ───────────────────────────────────────────────────
    html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">`;

    // Movies
    const mv = data.movies || {};
    html += `<div class="card" style="padding:20px">
      <div style="font-weight:600;margin-bottom:16px;display:flex;align-items:center;gap:10px">
        <img src="https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/radarr.png" width="20" height="20" style="border-radius:4px" onerror="this.style.display='none'">
        <span>${_('Films (hdr)','Movies')} <span style="font-size:11px;color:var(--muted);font-weight:400">(Radarr)</span></span>
      </div>
      <div style="display:flex;flex-direction:column;gap:8px">
        ${statRow(_('Total dans la bibliothèque','Total in library'), mv.total_in_library ?? '—')}
        ${statRow(_('Avec fichier','With file'), mv.count ?? '—', '#10b981')}
        ${statRow(_('Surveillés','Monitored'), mv.monitored ?? '—', 'var(--accent2)')}
        ${statRow(_('Non surveillés','Unmonitored'), mv.unmonitored ?? '—', '#94a3b8')}
        <div style="border-top:1px solid var(--border);margin:4px 0"></div>
        ${statRow(_('Espace utilisé','Space used'), fmtSize(mv.size || 0), '#e2e8f0')}
        ${statRow(_('Taille moyenne / film','Avg size / movie'), mv.count ? fmtSize((mv.size||0)/mv.count) : '—', '#e2e8f0')}
      </div>
    </div>`;

    // Series
    const sr = data.series || {};
    html += `<div class="card" style="padding:20px">
      <div style="font-weight:600;margin-bottom:16px;display:flex;align-items:center;gap:10px">
        <img src="https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/sonarr.png" width="20" height="20" style="border-radius:4px" onerror="this.style.display='none'">
        <span>${_('Séries (hdr)','Series')} <span style="font-size:11px;color:var(--muted);font-weight:400">(Sonarr)</span></span>
      </div>
      <div style="display:flex;flex-direction:column;gap:8px">
        ${statRow(_('Séries au total','Total series'), sr.count ?? '—')}
        ${statRow(_('Surveillées','Monitored'), sr.monitored ?? '—', 'var(--accent2)')}
        ${statRow(_('Non surveillées','Unmonitored'), sr.unmonitored ?? '—', '#94a3b8')}
        ${statRow(_('Épisodes (fichiers)','Episodes (files)'), sr.episodes ?? '—', '#10b981')}
        <div style="border-top:1px solid var(--border);margin:4px 0"></div>
        ${statRow(_('Espace utilisé','Space used'), fmtSize(sr.size || 0), '#e2e8f0')}
        ${statRow(_('Taille moyenne / série','Avg size / series'), sr.count ? fmtSize((sr.size||0)/sr.count) : '—', '#e2e8f0')}
      </div>
    </div>`;

    html += `</div>`;

    // ── Total + queue ──────────────────────────────────────────────────────
    const q = data.queue || {};
    const pending = q.pending || 0;
    const deleted = q.deleted || 0;
    html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
      <div class="card" style="padding:20px">
        <div style="font-weight:600;margin-bottom:14px;display:flex;align-items:center;gap:8px">
          <i class="fas fa-database" style="color:var(--accent2)"></i>${_('Total médias','Total media')}
        </div>
        <div style="font-size:28px;font-weight:700;color:#e2e8f0">${fmtSize(data.total_media_size)}</div>
        <div style="font-size:12px;color:var(--muted);margin-top:4px">${_('Films + Séries sur disque','Movies + Series on disk')}</div>
      </div>
      <div class="card" style="padding:20px;border:1px solid #f59e0b40;background:#f59e0b08">
        <div style="font-weight:600;margin-bottom:14px;display:flex;align-items:center;gap:8px">
          <i class="fas fa-recycle" style="color:#f59e0b"></i>${_('File de suppression','Deletion queue')}
        </div>
        <div style="display:flex;flex-direction:column;gap:6px">
          ${statRow(_('En attente','Pending'), pending, '#f59e0b')}
          ${statRow(_('Supprimés','Deleted'), deleted, '#10b981')}
          ${statRow(_('Exclus','Excluded'), q.excluded ?? 0, '#94a3b8')}
          ${statRow(_('Erreurs','Errors'), q.error ?? 0, '#ef4444')}
          ${q.reclaimable_size > 0 ? `<div style="border-top:1px solid var(--border);margin:6px 0"></div>
          <div style="display:flex;justify-content:space-between;align-items:center">
            <span style="font-size:13px;color:#f59e0b">💾 Espace récupérable</span>
            <span style="font-size:15px;font-weight:700;color:#f59e0b">${fmtSize(q.reclaimable_size)}</span>
          </div>
          <div style="font-size:11px;color:var(--muted)">si les ${q.reclaimable_count || 0} médias en attente sont supprimés</div>` : ''}
        </div>
        <button class="btn btn-ghost" style="font-size:12px;margin-top:10px" onclick="showPage('queue')">
          <i class="fas fa-hourglass-half"></i>${_('Voir la file','View queue')}
        </button>
      </div>
    </div>`;

    box.innerHTML = html;
  } catch(e) {
    console.error(e);
    box.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">Erreur chargement stockage</div>';
  }
}

function statRow(label, value, valueColor = 'var(--text)') {
  return `<div style="display:flex;justify-content:space-between;align-items:center">
    <span style="font-size:13px;color:var(--muted)">${label}</span>
    <span style="font-size:13px;font-weight:600;color:${valueColor}">${value}</span>
  </div>`;
}

// Patch showPage loaders
const _origShowPage = showPage;

// ─── Unmonitored ─────────────────────────────────────────────────────────────
let _unmonData = { movies: [], series: [] };
let _unmonFiltered = [];

async function loadUnmonitored() {
  document.getElementById('unmon-grid').innerHTML =
    '<div style="text-align:center;padding:60px;color:var(--muted);grid-column:1/-1"><i class="fas fa-spinner fa-spin" style="font-size:28px;display:block;margin-bottom:12px"></i>Chargement depuis Radarr et Sonarr...</div>';
  document.getElementById('unmon-stats').innerHTML = '';
  try {
    const data = await api('/api/unmonitored/');
    _unmonData = data;
    // Stats bar
    const totalSize = [...data.movies, ...data.series].reduce((s, m) => s + (m.size_on_disk || 0), 0);
    const withFile = [...data.movies, ...data.series].filter(m => m.has_file).length;
    document.getElementById('unmon-stats').innerHTML = `
      <div class="card stat-card"><div class="stat-label">Films non surveillés</div><div class="stat-num" style="color:#f59e0b">${data.movies.length}</div></div>
      <div class="card stat-card"><div class="stat-label">Séries non surveillées</div><div class="stat-num" style="color:#818cf8">${data.series.length}</div></div>
      <div class="card stat-card"><div class="stat-label">Espace occupé</div><div class="stat-num" style="color:#94a3b8;font-size:20px">${fmtSize(totalSize)}</div></div>`;
    filterUnmonitored();
  } catch(e) { toast('Erreur chargement non surveillés', 'error'); }
}

function filterUnmonitored() {
  const search = (document.getElementById('unmon-search')?.value || '').toLowerCase();
  const filter = document.getElementById('unmon-filter')?.value || '';
  let items = [..._unmonData.movies, ..._unmonData.series];
  if (filter === 'Movie') items = _unmonData.movies;
  if (filter === 'Series') items = _unmonData.series;
  if (search) items = items.filter(m => m.title.toLowerCase().includes(search));
  _unmonFiltered = items;
  sortUnmonitored();
}

function sortUnmonitored() {
  const sort = document.getElementById('unmon-sort')?.value || 'title';
  const items = [..._unmonFiltered];
  if (sort === 'title') items.sort((a,b) => a.title.localeCompare(b.title));
  else if (sort === 'title_desc') items.sort((a,b) => b.title.localeCompare(a.title));
  else if (sort === 'size') items.sort((a,b) => (b.size_on_disk||0)-(a.size_on_disk||0));
  else if (sort === 'added') items.sort((a,b) => (b.added||'').localeCompare(a.added||''));
  else if (sort === 'year') items.sort((a,b) => (b.year||0)-(a.year||0));
  renderUnmonitored(items);
}

function renderUnmonitored(items) {
  const grid = document.getElementById('unmon-grid');
  if (!items.length) {
    grid.innerHTML = '<div style="text-align:center;padding:60px;color:var(--muted);grid-column:1/-1"><i class="fas fa-check-circle" style="font-size:32px;color:#10b98180;display:block;margin-bottom:12px"></i>Aucun média non surveillé !</div>';
    return;
  }
  grid.innerHTML = items.map(m => {
    const isMovie = m.media_type === 'Movie';
    const icon = isMovie ? '🎬' : '📺';
    const sourceColor = isMovie ? '#f59e0b' : '#818cf8';
    const poster = m.poster_url
      ? `<img src="${m.poster_url}" style="width:100%;height:240px;object-fit:cover;display:block" onerror="this.parentElement.innerHTML='<div style=width:100%;height:240px;background:#ffffff08;display:flex;align-items:center;justify-content:center;font-size:40px>${icon}</div>'">`
      : `<div style="width:100%;height:240px;background:#ffffff08;display:flex;align-items:center;justify-content:center;font-size:40px">${icon}</div>`;
    const size = m.size_on_disk ? `<span style="font-size:10px;color:var(--muted)">${fmtSize(m.size_on_disk)}</span>` : '';
    const hasFileTag = m.has_file
      ? `<span style="font-size:10px;background:#10b98120;color:#10b981;border-radius:3px;padding:1px 5px">Fichier présent</span>`
      : `<span style="font-size:10px;background:#ef444420;color:#ef4444;border-radius:3px;padding:1px 5px">Pas de fichier</span>`;
    const subInfo = isMovie
      ? (m.studio || '')
      : `${m.seasons || 0} saison(s) · ${m.episode_count || 0} ép.`;
    const monitorBtn = isMovie
      ? `<button class="btn btn-primary" style="flex:1;justify-content:center;font-size:11px;padding:5px 8px" data-mid="${m.id}" data-title="${escapeHtml(m.title)}" data-type="movie" onclick="monitorFromEl(this)"><i class="fas fa-eye"></i>Surveiller</button>`
      : `<button class="btn btn-primary" style="flex:1;justify-content:center;font-size:11px;padding:5px 8px" data-mid="${m.id}" data-title="${escapeHtml(m.title)}" data-type="series" onclick="monitorFromEl(this)"><i class="fas fa-eye"></i>Surveiller</button>`;
    const deleteBtn = isMovie
      ? `<button class="btn btn-ghost" style="padding:5px 8px;font-size:11px" data-mid="${m.id}" data-title="${escapeHtml(m.title)}" data-type="movie" data-hasfile="${m.has_file}" onclick="deleteUnmonitoredFromEl(this)" title="Supprimer"><i class="fas fa-trash"></i></button>`
      : `<button class="btn btn-ghost" style="padding:5px 8px;font-size:11px" data-mid="${m.id}" data-title="${escapeHtml(m.title)}" data-type="series" data-hasfile="${m.has_file}" onclick="deleteUnmonitoredFromEl(this)" title="Supprimer"><i class="fas fa-trash"></i></button>`;

    return `<div class="card" style="overflow:hidden;display:flex;flex-direction:column" id="unmon-${m.source}-${m.id}">
      <div style="position:relative">${poster}
        <span style="position:absolute;top:6px;left:6px;background:${sourceColor};color:#0f1117;font-size:10px;font-weight:700;border-radius:4px;padding:2px 6px">${m.source}</span>
        ${m.year ? `<span style="position:absolute;top:6px;right:6px;background:#00000099;color:#e2e8f0;font-size:10px;border-radius:4px;padding:2px 6px">${m.year}</span>` : ''}
      </div>
      <div style="padding:10px;flex:1;display:flex;flex-direction:column;gap:6px">
        <div style="font-weight:600;font-size:13px;color:#e2e8f0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escapeHtml(m.title)}">${escapeHtml(m.title)}</div>
        <div style="display:flex;align-items:center;justify-content:space-between">
          ${hasFileTag}
          ${size}
        </div>
        ${subInfo ? `<div style="font-size:11px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${subInfo}</div>` : ''}
        ${m.genres?.length ? `<div style="display:flex;gap:3px;flex-wrap:wrap">${m.genres.map(g=>`<span style="font-size:10px;background:#ffffff0a;color:var(--muted);border-radius:3px;padding:1px 5px">${escapeHtml(g)}</span>`).join('')}</div>` : ''}
        <div style="display:flex;gap:4px;margin-top:auto;padding-top:6px">
          ${monitorBtn}${deleteBtn}
        </div>
      </div>
    </div>`;
  }).join('');
}


function monitorFromEl(el) {
  if (el.dataset.type === 'series') monitorSeries(parseInt(el.dataset.mid), el.dataset.title);
  else monitorMovie(parseInt(el.dataset.mid), el.dataset.title);
}
function deleteUnmonitoredFromEl(el) {
  deleteUnmonitored(el.dataset.type, parseInt(el.dataset.mid), el.dataset.title, el.dataset.hasfile === 'true');
}
async function monitorMovie(id, title) {
  try {
    await api(`/api/unmonitored/monitor/movie/${id}`, 'POST');
    toast(`✅ "${title}" remis en surveillance`, 'success');
    // Remove card with animation
    const card = document.getElementById(`unmon-Radarr-${id}`);
    if (card) { card.style.opacity='0'; card.style.transition='opacity .3s'; setTimeout(()=>card.remove(),300); }
    _unmonData.movies = _unmonData.movies.filter(m => m.id !== id);
  } catch(e) { toast(`Erreur : ${e.message}`, 'error'); }
}

async function monitorSeries(id, title) {
  try {
    await api(`/api/unmonitored/monitor/series/${id}`, 'POST');
    toast(`✅ "${title}" remise en surveillance`, 'success');
    const card = document.getElementById(`unmon-Sonarr-${id}`);
    if (card) { card.style.opacity='0'; card.style.transition='opacity .3s'; setTimeout(()=>card.remove(),300); }
    _unmonData.series = _unmonData.series.filter(m => m.id !== id);
  } catch(e) { toast(`Erreur : ${e.message}`, 'error'); }
}

async function deleteUnmonitored(type, id, title, hasFile) {
  const msg = hasFile
    ? `Supprimer "${title}" ?\n\n⚠️ Ce média a des fichiers sur disque.\n\nCochez pour supprimer aussi les fichiers.`
    : `Supprimer "${title}" de ${type === 'movie' ? 'Radarr' : 'Sonarr'} ?`;
  try { await showConfirm({ title: hasFile ? 'Supprimer "' + title + '" ?' : 'Supprimer de ' + (type === 'movie' ? 'Radarr' : 'Sonarr') + ' ?', body: hasFile ? '⚠️ Ce média a des fichiers sur disque.' : 'Le média sera retiré du gestionnaire.', icon: 'trash', color: '#ef4444', okLabel: 'Supprimer' }); } catch(e) { return; }
  let deleteFiles = false;
  if (hasFile) { try { await showConfirm({ title: 'Supprimer aussi les fichiers ?', body: 'Les fichiers de <strong>' + escapeHtml(title) + '</strong> seront effacés du disque.', detail: '⚠️ Cette action est irréversible — les fichiers seront définitivement supprimés.', icon: 'hard-drive', color: '#f59e0b', okLabel: 'Supprimer les fichiers', okClass: 'btn-danger' }); deleteFiles = true; } catch(e) { deleteFiles = false; } }
  try {
    await api(`/api/unmonitored/${type}/${id}?delete_files=${deleteFiles}`, 'DELETE');
    toast(`🗑️ "${title}" supprimé`, 'success');
    const src = type === 'movie' ? 'Radarr' : 'Sonarr';
    const card = document.getElementById(`unmon-${src}-${id}`);
    if (card) { card.style.opacity='0'; card.style.transition='opacity .3s'; setTimeout(()=>card.remove(),300); }
    if (type === 'movie') _unmonData.movies = _unmonData.movies.filter(m => m.id !== id);
    else _unmonData.series = _unmonData.series.filter(m => m.id !== id);
  } catch(e) { toast(`Erreur : ${e.message}`, 'error'); }
}
