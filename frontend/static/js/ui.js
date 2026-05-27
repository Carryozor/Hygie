// ─── Image proxy helper ───────────────────────────────────────────────────────
// Route external images through the backend proxy to avoid browser proxy issues.
function proxyImg(url) {
  if (!url) return '';
  if (url.startsWith('/') || url.startsWith('data:')) return url;
  return '/api/proxy/image?url=' + encodeURIComponent(url);
}

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

// ─── Confirmation modal ───────────────────────────────────────────────────────
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

function _closeConfirm() {
  document.getElementById('modal-confirm').style.display = 'none';
}

document.addEventListener('keydown', e => {
  if (document.getElementById('modal-confirm') && document.getElementById('modal-confirm').style.display !== 'none') {
    if (e.key === 'Enter') { e.preventDefault(); _confirmResolve(); }
    if (e.key === 'Escape') _confirmReject();
  }
});

// ─── Sidebar dry-run indicator ────────────────────────────────────────────────
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
    const settingsToggle = document.getElementById('dry-run-toggle');
    if (settingsToggle) settingsToggle.checked = enabled;
    toast(enabled ? 'Dry Run activé — aucune suppression réelle' : 'Dry Run désactivé', enabled ? 'warn' : 'info');
  } catch(e) { toast('Erreur','error'); }
}
