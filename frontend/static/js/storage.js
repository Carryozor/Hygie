// ─── Storage ──────────────────────────────────────────────────────────────────
let _storageCached = null;  // JS-side cache: avoid re-fetching within the same session load
function fmtSize(bytes) {
  if (!bytes) return '0 B';
  const units = ['B','KB','MB','GB','TB'];
  let i=0; let v=bytes;
  while(v>=1024&&i<units.length-1){v/=1024;i++;}
  return `${v.toFixed(i>1?2:0)} ${units[i]}`;
}

function statRow(label, value, valueColor = 'var(--text)') {
  return `<div style="display:flex;justify-content:space-between;align-items:center">
    <span style="font-size:13px;color:var(--muted)">${label}</span>
    <span style="font-size:13px;font-weight:600;color:${valueColor}">${value}</span>
  </div>`;
}

async function loadStorage() {
  const box = document.getElementById('storage-content');
  box.innerHTML = `
    <div class="card" style="padding:20px">
      <div class="skeleton" style="height:18px;width:38%;margin-bottom:20px"></div>
      <div style="display:flex;flex-direction:column;gap:16px">
        ${[1,2,3].map(()=>`<div>
          <div style="display:flex;justify-content:space-between;margin-bottom:8px">
            <div class="skeleton" style="height:13px;width:42%"></div>
            <div class="skeleton" style="height:22px;width:10%"></div>
          </div>
          <div class="skeleton" style="height:10px;width:100%"></div>
          <div style="display:flex;justify-content:space-between;margin-top:6px">
            <div class="skeleton" style="height:11px;width:18%"></div>
            <div class="skeleton" style="height:11px;width:18%"></div>
          </div>
        </div>`).join('')}
      </div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
      ${[1,2].map(()=>`<div class="card" style="padding:20px">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px">
          <div class="skeleton" style="height:20px;width:20px;border-radius:4px"></div>
          <div class="skeleton" style="height:16px;width:45%"></div>
        </div>
        ${[1,2,3,4,5].map(()=>`<div style="display:flex;justify-content:space-between;margin-bottom:10px">
          <div class="skeleton" style="height:13px;width:48%"></div>
          <div class="skeleton" style="height:13px;width:18%"></div>
        </div>`).join('')}
      </div>`).join('')}
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
      ${[1,2].map(()=>`<div class="card" style="padding:20px">
        <div class="skeleton" style="height:16px;width:50%;margin-bottom:14px"></div>
        <div class="skeleton" style="height:32px;width:55%;margin-bottom:6px"></div>
        <div class="skeleton" style="height:12px;width:70%"></div>
      </div>`).join('')}
    </div>`;
  if (_storageCached) {
    renderStorage(_storageCached, box);
  }
  try {
    const data = await api('/api/storage');
    _storageCached = data;
    renderStorage(data, box);
  } catch(e) {
    if (!_storageCached) {
      console.error(e);
      box.innerHTML = `<div style="text-align:center;padding:40px;color:var(--muted)">${t('Erreur chargement stockage')}</div>`;
    }
  }
}

function renderStorage(data, box) {
  let html = '';

  const disks = data.disks || [];
  if (disks.length) {
    html += `<div class="card" style="padding:20px">
      <div style="font-weight:600;margin-bottom:16px;display:flex;align-items:center;gap:8px">
        <i class="fas fa-hard-drive" style="color:var(--accent2)"></i>${t('Utilisation disque')}
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
                <div style="font-size:11px;color:var(--muted);margin-top:2px">${f.source}${!accessible?` · <span style="color:#ef4444">${t('Non accessible')}</span>`:''}</div>
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
              <span style="font-size:11px;color:var(--muted)">${fmtSize(f.free)} ${t('libres')}</span>
              <span style="font-size:11px;color:var(--muted)">${fmtSize(f.total)} ${t('total')}</span>
            </div>
          </div>`;
        }).join('<div style="border-top:1px solid var(--border)"></div>')}
      </div>
    </div>`;
  } else {
    html += `<div class="card" style="padding:20px;text-align:center;color:var(--muted)">
      <i class="fas fa-hard-drive" style="font-size:24px;margin-bottom:8px;display:block"></i>
      ${t('Données disque non disponibles — vérifiez la configuration Radarr/Sonarr')}
    </div>`;
  }

  html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">`;

  const mv = data.movies || {};
  html += `<div class="card" style="padding:20px">
    <div style="font-weight:600;margin-bottom:16px;display:flex;align-items:center;gap:10px">
      <img src="/static/img/icons/radarr.png" width="20" height="20" style="border-radius:4px" onerror="this.style.display='none'">
      <span>${t('Films (hdr)')} <span style="font-size:11px;color:var(--muted);font-weight:400">(Radarr)</span></span>
    </div>
    <div style="display:flex;flex-direction:column;gap:8px">
      ${statRow(t('Total dans la bibliothèque'), mv.total_in_library ?? '—')}
      ${statRow(t('Avec fichier'), mv.count ?? '—', '#10b981')}
      ${statRow(t('Surveillés'), mv.monitored ?? '—', 'var(--accent2)')}
      ${statRow(t('Non surveillés'), mv.unmonitored ?? '—', '#94a3b8')}
      <div style="border-top:1px solid var(--border);margin:4px 0"></div>
      ${statRow(t('Espace utilisé'), fmtSize(mv.size || 0), '#e2e8f0')}
      ${statRow(t('Taille moyenne / film'), mv.count ? fmtSize((mv.size||0)/mv.count) : '—', '#e2e8f0')}
    </div>
  </div>`;

  const sr = data.series || {};
  html += `<div class="card" style="padding:20px">
    <div style="font-weight:600;margin-bottom:16px;display:flex;align-items:center;gap:10px">
      <img src="/static/img/icons/sonarr.png" width="20" height="20" style="border-radius:4px" onerror="this.style.display='none'">
      <span>${t('Séries (hdr)')} <span style="font-size:11px;color:var(--muted);font-weight:400">(Sonarr)</span></span>
    </div>
    <div style="display:flex;flex-direction:column;gap:8px">
      ${statRow(t('Séries au total'), sr.count ?? '—')}
      ${statRow(t('Surveillées'), sr.monitored ?? '—', 'var(--accent2)')}
      ${statRow(t('Non surveillées'), sr.unmonitored ?? '—', '#94a3b8')}
      ${statRow(t('Épisodes (fichiers)'), sr.episodes ?? '—', '#10b981')}
      <div style="border-top:1px solid var(--border);margin:4px 0"></div>
      ${statRow(t('Espace utilisé'), fmtSize(sr.size || 0), '#e2e8f0')}
      ${statRow(t('Taille moyenne / série'), sr.count ? fmtSize((sr.size||0)/sr.count) : '—', '#e2e8f0')}
    </div>
  </div>`;

  html += `</div>`;

  const q = data.queue || {};
  const pending = q.pending || 0;
  const deleted = q.deleted || 0;
  html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
    <div class="card" style="padding:20px">
      <div style="font-weight:600;margin-bottom:14px;display:flex;align-items:center;gap:8px">
        <i class="fas fa-database" style="color:var(--accent2)"></i>${t('Total médias')}
      </div>
      <div style="font-size:28px;font-weight:700;color:#e2e8f0">${fmtSize(data.total_media_size)}</div>
      <div style="font-size:12px;color:var(--muted);margin-top:4px">${t('Films + Séries sur disque')}</div>
    </div>
    <div class="card" style="padding:20px;border:1px solid #f59e0b40;background:#f59e0b08">
      <div style="font-weight:600;margin-bottom:14px;display:flex;align-items:center;gap:8px">
        <i class="fas fa-recycle" style="color:#f59e0b"></i>${t('File de suppression')}
      </div>
      <div style="display:flex;flex-direction:column;gap:6px">
        ${statRow(t('En attente'), pending, '#f59e0b')}
        ${statRow(t('Supprimés'), deleted, '#10b981')}
        ${statRow(t('Exclus'), q.excluded ?? 0, '#94a3b8')}
        ${statRow(t('Erreurs'), q.error ?? 0, '#ef4444')}
        ${q.reclaimable_size > 0 ? `<div style="border-top:1px solid var(--border);margin:6px 0"></div>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span style="font-size:13px;color:#f59e0b">${t('💾 Espace récupérable')}</span>
          <span style="font-size:15px;font-weight:700;color:#f59e0b">${fmtSize(q.reclaimable_size)}</span>
        </div>
        <div style="font-size:11px;color:var(--muted)">${t('si les')} ${q.reclaimable_count || 0} ${t('médias en attente sont supprimés')}</div>` : ''}
      </div>
      <button class="btn btn-ghost" style="font-size:12px;margin-top:10px" onclick="showPage('queue')">
        <i class="fas fa-hourglass-half"></i>${t('Voir la file')}
      </button>
    </div>
  </div>`;

  box.innerHTML = html;
}
