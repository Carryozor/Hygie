// ─── Seerr users cache ────────────────────────────────────────────────────────
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
const OP_LABELS = { gt:'>',gte:'≥',lt:'<',lte:'≤',eq:'=' };
function _fieldLabel(key) {
  const map = { days_since_added:'Ajouté depuis',days_not_watched:'Non vu depuis',play_count:'Nombre de lectures',never_watched:'Jamais regardé' };
  return t(map[key] || key);
}
const FIELD_LABELS = new Proxy({}, { get(obj, k) { return _fieldLabel(k); } });
let editingLibId = null, conditions = [], seerrConditions = [], availableSeerrUsers = [];

async function loadLibraries() {
  try {
    const libs = await api('/api/libraries/');
    const box = document.getElementById('libraries-list');
    if (!libs.length) {
      box.innerHTML = `<div class="card" style="padding:40px;text-align:center;color:var(--muted)"><i class="fas fa-layer-group" style="font-size:32px;margin-bottom:12px;display:block;color:var(--border)"></i>${t('Aucune bibliothèque.')}</div>`;
      return;
    }
    box.innerHTML = libs.map(l => {
      const condText = (l.conditions||[]).map(c =>
        `<span style="background:#6366f120;border:1px solid #6366f140;border-radius:6px;padding:2px 8px;font-size:11px;color:var(--accent2)">${FIELD_LABELS[c.field]||c.field} ${OP_LABELS[c.op]||c.op} ${c.value}${['days_since_added','days_not_watched'].includes(c.field)?t('j'):'d'}</span>`
      ).join(`<span style="font-size:11px;color:var(--muted);padding:0 4px">${l.logic==='OR'?t('OU'):t('ET')}</span>`);
      const seerrText = (l.seerr_conditions||[]).length
        ? `<span style="font-size:11px;color:#a78bfa;margin-left:6px"><i class="fas fa-user-check" style="margin-right:3px"></i>${l.seerr_conditions.length} ${t('filtre(s) Seerr')}</span>` : '';
      return `<div class="card" style="padding:18px;display:flex;align-items:center;justify-content:space-between;gap:16px">
        <div style="display:flex;align-items:center;gap:14px;flex:1;min-width:0">
          <div style="width:36px;height:36px;border-radius:10px;background:${l.enabled?'#6366f118':'#ffffff0a'};display:flex;align-items:center;justify-content:center;flex-shrink:0">
            <i class="fas fa-layer-group" style="color:${l.enabled?'var(--accent2)':'var(--muted)'}"></i>
          </div>
          <div style="min-width:0">
            <div style="font-weight:600;color:#e2e8f0;margin-bottom:4px">${escapeHtml(l.name)}</div>
            <div style="display:flex;flex-wrap:wrap;gap:4px;align-items:center">${condText||`<span style="font-size:11px;color:var(--muted);font-style:italic">${t('Aucune condition')}</span>`}${seerrText}</div>
            <div style="font-size:11px;color:var(--muted);margin-top:4px">${t('Délai de grâce')} : ${l.grace_days}${t('j')}${!l.enabled?(' '+t('· Désactivé')):''}</div>
          </div>
        </div>
        <div style="display:flex;gap:6px;flex-shrink:0">
          <button class="btn btn-ghost" style="padding:6px 10px" title="${l.enabled?t('Désactiver'):t('Activer')}" onclick="toggleLibrary('${l.id}',${!l.enabled})">
            <i class="fas fa-${l.enabled?'toggle-on':'toggle-off'}" style="color:${l.enabled?'#10b981':'#94a3b8'}"></i>
          </button>
          <button class="btn btn-ghost" style="padding:6px 10px" title="${t('Scanner')}" onclick="triggerScanLibrary('${l.id}',this.dataset.n)" data-n="${escapeHtml(l.name)}"><i class="fas fa-magnifying-glass"></i></button>
          <button class="btn btn-ghost" style="padding:6px 10px" title="${t('Cloner')}" onclick="cloneLibrary('${l.id}')"><i class="fas fa-clone"></i></button>
          <button class="btn btn-ghost" style="padding:6px 10px" title="${t('Modifier')}" onclick="editLibrary('${l.id}')"><i class="fas fa-pen"></i></button>
          <button class="btn btn-ghost" style="padding:6px 10px;color:#ef4444" title="${t('Supprimer')}" onclick="deleteLibrary('${l.id}')"><i class="fas fa-trash"></i></button>
        </div>
      </div>`;
    }).join('');
  } catch(e) { toast(t('Erreur bibliothèques'),'error'); }
}

function renderConditions() {
  const box = document.getElementById('conditions-container');
  if (!conditions.length) {
    box.innerHTML = `<div style="font-size:12px;color:var(--muted);text-align:center;padding:10px;background:#0a0c14;border-radius:8px;border:1px dashed var(--border)">${t('Cliquez sur "Ajouter" pour créer une condition')}</div>`;
    return;
  }
  box.innerHTML = conditions.map((c,i) => {
    const isNever = c.field==='never_watched';
    return `<div class="condition-row">
      <select class="select" style="flex:2" onchange="conditions[${i}].field=this.value;renderConditions()">
        <option value="days_since_added" ${c.field==='days_since_added'?'selected':''}>${t('Ajouté depuis (jours)')}</option>
        <option value="days_not_watched" ${c.field==='days_not_watched'?'selected':''}>${t('Non vu depuis (jours)')}</option>
        <option value="play_count" ${c.field==='play_count'?'selected':''}>${t('Nombre de lectures')}</option>
        <option value="never_watched" ${c.field==='never_watched'?'selected':''}>${t('Jamais regardé')}</option>
      </select>
      ${isNever
        ? `<select class="select" style="flex:1" onchange="conditions[${i}].value=parseInt(this.value)"><option value="1" ${c.value===1?'selected':''}>= vrai</option><option value="0" ${c.value===0?'selected':''}>= faux</option></select><div style="flex:1"></div>`
        : `<select class="select" style="flex:1" onchange="conditions[${i}].op=this.value">
            <option value="gt" ${c.op==='gt'?'selected':''}>${t('> supérieur')}</option>
            <option value="gte" ${c.op==='gte'?'selected':''}>${t('>= sup. ou égal')}</option>
            <option value="lt" ${c.op==='lt'?'selected':''}>${t('< inférieur')}</option>
            <option value="lte" ${c.op==='lte'?'selected':''}>${t('<= inf. ou égal')}</option>
            <option value="eq" ${c.op==='eq'?'selected':''}>${t('= égal')}</option>
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
    box.innerHTML = `<div style="font-size:11px;color:var(--muted);padding:8px;background:#0a0c14;border-radius:6px">${t('Configurez Seerr dans les paramètres pour activer le filtrage.')}</div>`;
    return;
  }
  if (!seerrConditions.length) {
    box.innerHTML = `<div style="font-size:12px;color:var(--muted);text-align:center;padding:10px;background:#0a0c14;border-radius:8px;border:1px dashed var(--border)">${t('Aucun filtre — tous les utilisateurs inclus')}</div>`;
    return;
  }
  box.innerHTML = seerrConditions.map((c,i) => `
    <div class="condition-row">
      <select class="select" style="flex:1" onchange="seerrConditions[${i}].type=this.value">
        <option value="user_include" ${c.type==='user_include'?'selected':''}>${t('Inclure uniquement')}</option>
        <option value="user_exclude" ${c.type==='user_exclude'?'selected':''}>${t('Exclure')}</option>
      </select>
      <select class="select" style="flex:2" onchange="seerrConditions[${i}].user_id=parseInt(this.value);seerrConditions[${i}].username=this.options[this.selectedIndex].text">
        ${availableSeerrUsers.map(u=>`<option value="${u.id}" ${u.id===c.user_id?'selected':''}>${u.username}</option>`).join('')}
      </select>
      <button class="btn btn-ghost" style="padding:5px 8px;color:#ef4444;flex-shrink:0" onclick="removeSeerrCondition(${i})"><i class="fas fa-times"></i></button>
    </div>`).join('');
}
function addSeerrCondition() {
  if (!availableSeerrUsers.length) { toast(t('Aucun utilisateur Seerr disponible'),'warn'); return; }
  seerrConditions.push({type:'user_include',user_id:availableSeerrUsers[0].id,username:availableSeerrUsers[0].username});
  renderSeerrConditions();
}
function removeSeerrCondition(i) { seerrConditions.splice(i,1); renderSeerrConditions(); }

async function openAddLibrary() {
  editingLibId=null; conditions=[{field:'days_since_added',op:'gt',value:30},{field:'days_not_watched',op:'gt',value:60}]; seerrConditions=[];
  document.getElementById('modal-title').textContent=t('Ajouter une bibliothèque');
  document.getElementById('lib-name').value=''; document.getElementById('lib-grace').value=7; document.getElementById('lib-logic').value='AND';
  document.getElementById('lib-deletion-unit').value='episode';
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
  document.getElementById('modal-title').textContent=t('Modifier la bibliothèque');
  document.getElementById('lib-name').value=lib.name;
  document.getElementById('lib-grace').value=lib.grace_days??7;
  document.getElementById('lib-logic').value=lib.logic||'AND';
  document.getElementById('lib-deletion-unit').value=lib.deletion_unit||'episode';
  renderConditions();
  await loadEmbyLibOptions(lib.emby_library_id);
  availableSeerrUsers = await getSeerrUsers();
  renderSeerrConditions();
  document.getElementById('modal-library').style.display='flex';
}
async function loadEmbyLibOptions(selected='') {
  const sel = document.getElementById('lib-emby-id');
  sel.innerHTML=`<option value="">${t('Chargement...')}</option>`;
  try {
    const libs = await api('/api/libraries/emby');
    sel.innerHTML = libs.map(l=>`<option value="${l.id}">${escapeHtml(l.name)}${l.type?' ('+escapeHtml(l.type)+')':''}</option>`).join('');
    if (selected) { sel.value=selected; if (!sel.value) { const o=document.createElement('option'); o.value=selected; o.textContent=`(ID: ${selected})`; o.selected=true; sel.prepend(o); } }
  } catch(e) { sel.innerHTML=`<option value="">${t('Erreur Emby')}</option>`; }
}
async function saveLibrary() {
  const body = { name:document.getElementById('lib-name').value, emby_library_id:document.getElementById('lib-emby-id').value, grace_days:parseInt(document.getElementById('lib-grace').value)||7, logic:document.getElementById('lib-logic').value, deletion_unit:document.getElementById('lib-deletion-unit').value||'episode', conditions, seerr_conditions:seerrConditions, enabled:true };
  if (!body.name||!body.emby_library_id) { toast(t('Remplissez tous les champs'),'warn'); return; }
  try {
    const savedId = editingLibId;
    if (editingLibId) await api(`/api/libraries/${editingLibId}`,'PUT',body);
    else await api('/api/libraries/','POST',body);
    toast(editingLibId?t('Mise à jour'):t('Bibliothèque ajoutée'),'success');
    closeModal(); loadLibraries();
    if (savedId) { const r=await api(`/api/libraries/${savedId}/reevaluate`,'POST'); if (r.removed>0) toast(`${r.removed} ${t('média(s) retiré(s) de la file')}`,'info'); }
  } catch(e) { toast(t('Erreur sauvegarde'),'error'); }
}
async function deleteLibrary(id) {
  try { await showConfirm({ title: t('Supprimer cette bibliothèque ?'), body: t("La bibliothèque et ses règles seront supprimées. Les médias déjà en file d'attente ne seront pas affectés."), icon: 'layer-group', color: '#ef4444', okLabel: t('Supprimer') }); } catch(e) { return; }
  await api(`/api/libraries/${id}`,'DELETE'); toast(t('Supprimée'),'success'); loadLibraries();
}
async function cloneLibrary(id) {
  try {
    await api(`/api/libraries/${id}/clone`, 'POST');
    toast(t('Clonée'), 'success');
    await loadLibraries();
  } catch(e) { toast(t('Erreur clonage') + ' : ' + e.message, 'error'); }
}
async function toggleLibrary(id, newEnabled) {
  await api(`/api/libraries/${id}`,'PUT',{enabled: newEnabled});
  toast(newEnabled ? t('Bibliothèque activée') : t('Bibliothèque désactivée'), 'success');
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
