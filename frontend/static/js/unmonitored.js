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
    const totalSize = [...data.movies, ...data.series].reduce((s, m) => s + (m.size_on_disk || 0), 0);
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
      ? `<img src="${escapeHtml(proxyImg(m.poster_url))}" style="width:100%;height:240px;object-fit:cover;display:block" onerror="this.parentElement.innerHTML='<div style=width:100%;height:240px;background:#ffffff08;display:flex;align-items:center;justify-content:center;font-size:40px>${icon}</div>'">`
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
