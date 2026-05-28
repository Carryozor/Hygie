// ─── Dashboard ────────────────────────────────────────────────────────────────
let _scanIntervalMin = 360, _delIntervalMin = 60;

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
      box.innerHTML = `<div style="text-align:center;padding:24px;color:var(--muted)"><i class="fas fa-circle-check" style="font-size:24px;color:#10b98180;display:block;margin-bottom:8px"></i>${t('Aucun média en attente')}</div>`;
      return;
    }
    box.innerHTML = data.items.map(m => {
      const _delDt = new Date(m.delete_at); const _now = new Date();
      const days = Math.max(0, Math.round((Date.UTC(_delDt.getUTCFullYear(),_delDt.getUTCMonth(),_delDt.getUTCDate()) - Date.UTC(_now.getUTCFullYear(),_now.getUTCMonth(),_now.getUTCDate())) / 86400000));
      const col = daysColorStyle(days);
      const icon = m.media_type==='Movie'?'🎬':'📺';
      const _title = escapeHtml(m.title);
      const _lib = escapeHtml(m.library_name || m.library_id);
      const poster = m.poster_url ? `<img src="${escapeHtml(proxyImg(m.poster_url))}" style="width:28px;height:42px;object-fit:cover;border-radius:3px;flex-shrink:0" onerror="this.style.display='none'">` : '';
      const req = m.seerr_username ? `<span style="font-size:11px;color:var(--muted)">👤 ${escapeHtml(m.seerr_username)}</span>` : '';
      const titleEl = m.seerr_request_url
        ? `<a href="${escapeHtml(m.seerr_request_url)}" target="_blank" style="color:#e2e8f0;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-decoration:none;display:block" onclick="event.stopPropagation()">${icon} ${_title}</a>`
        : `<div style="color:#e2e8f0;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${icon} ${_title}</div>`;
      const delLabel = days<=0 ? t('Suppression aujourd\'hui') : `${t('dans')} ${days}${t('j')}`;
      return `<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)">
        ${poster}
        <div style="flex:1;min-width:0">
          ${titleEl}
          <div style="display:flex;gap:8px;margin-top:2px">${req}<span style="font-size:11px;color:var(--muted)">📚 ${_lib}</span></div>
        </div>
        <div style="text-align:right;flex-shrink:0">
          <div style="${col};font-weight:600;font-size:12px">${delLabel}</div>
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
    _scanIntervalMin = parseInt(s.scan_interval_minutes || '360');
    _delIntervalMin  = parseInt(s.deletion_check_interval_minutes || '60');
    loadSchedulerInfo();
  } catch(e) {}
}

function _fmtCountdown(diffMin) {
  if (diffMin <= 0) return t('Imminent');
  if (diffMin < 60) return `${t('dans')} ${diffMin}min`;
  const h = Math.floor(diffMin / 60), m = diffMin % 60;
  return `${t('dans')} ${h}h${m > 0 ? String(m).padStart(2,'0') : ''}`;
}

async function loadSchedulerInfo() {
  try {
    const [jobs, status] = await Promise.all([
      api('/api/scheduler/status'),
      api('/api/media/job-status').catch(() => ({})),
    ]);
    const scanJob = jobs.find(j => j.id === 'scan_job');
    const delJob  = jobs.find(j => j.id === 'deletion_job');

    const scanEl  = document.getElementById('scan-countdown');
    const scanBar = document.getElementById('scan-progress-bar');
    if (status.scan_running) {
      if (scanEl) scanEl.textContent = t('En cours...');
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

    const delEl  = document.getElementById('del-countdown');
    const delBar = document.getElementById('del-progress-bar');
    if (status.deletion_running) {
      if (delEl) delEl.textContent = t('En cours...');
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
