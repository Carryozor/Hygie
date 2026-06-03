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
