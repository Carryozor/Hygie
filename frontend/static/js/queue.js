// ─── Queue ────────────────────────────────────────────────────────────────────
let selectedIds = new Set();
let queuePageSize = 50;
let queueTotalItems = 0;
let queueOffset = 0;
let queueSortCol = 'delete_at';
let queueSortDir = 'asc';
try { const _s=JSON.parse(sessionStorage.getItem('hq_sort')||'{}'); if(_s.col){queueSortCol=_s.col;queueSortDir=_s.dir||'asc';} } catch(e){}
let _searchTimer = null;
let queueViewMode = 'list';

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
          ? `<img src="${escapeHtml(proxyImg(m.poster_url))}" style="width:100%;height:220px;object-fit:cover;border-radius:8px 8px 0 0" onerror="this.style.display='none'">`
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
          ? `<img src="${escapeHtml(proxyImg(m.poster_url))}" style="width:32px;height:48px;object-fit:cover;border-radius:3px;flex-shrink:0" onerror="this.style.display='none'">`
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
