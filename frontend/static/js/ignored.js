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
        ${t('Aucun média ignoré.')}<br><span style="font-size:12px">${t('Utilisez le bouton')} <i class="fas fa-ban"></i> ${t('dans la file d\'attente pour ignorer définitivement un média.')}</span>
      </div>`;
      return;
    }
    box.innerHTML = items.map(item => {
      const poster = item.poster_url
        ? `<img src="${escapeHtml(proxyImg(item.poster_url))}" style="width:48px;height:72px;object-fit:cover;border-radius:6px;flex-shrink:0" onerror="this.style.display='none'">`
        : `<div style="width:48px;height:72px;background:#ffffff08;border-radius:6px;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:20px">${item.media_type==='Movie'?'🎬':'📺'}</div>`;
      const lib = item.library_name || item.library_id || '?';
      const dt = item.ignored_at ? new Date(item.ignored_at).toLocaleDateString('fr-FR',{day:'numeric',month:'short',year:'numeric'}) : '?';
      let expireBadge = '';
      if (item.expire_at) {
        const expDt = new Date(item.expire_at);
        const daysLeft = Math.ceil((expDt - Date.now()) / 86400000);
        const expStr = expDt.toLocaleDateString('fr-FR',{day:'numeric',month:'short',year:'numeric'});
        const col = daysLeft <= 7 ? '#ef4444' : daysLeft <= 30 ? '#f59e0b' : '#10b981';
        expireBadge = `<div style="font-size:12px;color:${col};margin-top:3px"><i class="fas fa-clock" style="font-size:10px;margin-right:4px"></i>${t('Expire le')} ${expStr} (${daysLeft > 0 ? t('dans')+' '+daysLeft+t('j') : t("aujourd'hui")})</div>`;
      } else {
        expireBadge = `<div style="font-size:12px;color:var(--muted);margin-top:3px"><i class="fas fa-infinity" style="font-size:10px;margin-right:4px"></i>${t('Ignoré définitivement')}</div>`;
      }
      return `<div class="card" style="padding:14px;display:flex;align-items:center;gap:14px">
        ${poster}
        <div style="flex:1;min-width:0">
          <div style="font-weight:600;color:#e2e8f0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(item.title)}</div>
          <div style="font-size:12px;color:var(--muted);margin-top:3px">📚 ${lib} · ${t('Ignoré le')} ${dt}</div>
          ${expireBadge}
          ${item.reason ? `<div style="font-size:12px;color:#f59e0b;margin-top:3px;display:flex;align-items:center;gap:4px"><i class="fas fa-comment-dots" style="font-size:10px"></i>${escapeHtml(item.reason)}</div>` : ''}
        </div>
        <button class="btn btn-ghost" style="padding:7px 12px;flex-shrink:0;color:#10b981" data-id="${item.id}" data-title="${escapeHtml(item.title)}" onclick="unignoreMediaFromEl(this)">
          <i class="fas fa-rotate-left"></i>${t('Remettre')}
        </button>
      </div>`;
    }).join('');
  } catch(e) { toast(t('Erreur chargement ignorés'),'error'); }
}

function unignoreMediaFromEl(el) {
  unignoreMedia(parseInt(el.dataset.id), el.dataset.title);
}
async function unignoreMedia(id, title) {
  try { await showConfirm({ title: t('Remettre en file d\'attente ?'), body: escapeHtml(title), icon: 'rotate-left', color: '#10b981', okLabel: t('Remettre'), okClass: 'btn-primary' }); } catch(e) { return; }
  try {
    await api(`/api/ignored/${id}/requeue`, 'POST');
    toast(`"${title}" ${t('remis en file d\'attente')}`, 'success');
  } catch(e) {
    await api(`/api/ignored/${id}`, 'DELETE');
    toast(`"${title}" ${t('retiré des ignorés — sera détecté au prochain scan')}`, 'info');
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
    ? `<img src="${escapeHtml(proxyImg(posterUrl))}" style="width:40px;height:60px;object-fit:cover;border-radius:4px;flex-shrink:0" onerror="this.style.display='none'">`
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
    toast(expireDays > 0 ? `${t('Ignoré pour')} ${expireDays} ${t('jours')}` : t('Ignoré définitivement'), 'success');
    closeIgnoreModal();
    loadQueue();
    loadDashboard();
  } catch(e) { toast(t('Erreur'),'error'); }
}

document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && document.getElementById('modal-ignore').style.display !== 'none') {
    confirmIgnore();
  }
});
