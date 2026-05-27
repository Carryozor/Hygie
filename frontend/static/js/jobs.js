// ─── Jobs ─────────────────────────────────────────────────────────────────────
async function loadJobs() {
  try {
    const [history,sched]=await Promise.all([api('/api/jobs/history'),api('/api/scheduler/status')]);
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
