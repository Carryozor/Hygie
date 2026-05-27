// ─── Auth ─────────────────────────────────────────────────────────────────────
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
  setTimeout(loadSchedulerInfo, 100);
  fetch('/api/version').then(r=>r.json()).then(v=>{
    const el = document.getElementById('app-version');
    if(el) el.textContent = `v${v.version}`;
  }).catch(()=>{});
}

async function doSetup() {
  const u = document.getElementById('auth-username').value.trim();
  const p = document.getElementById('auth-password').value;
  if (!u || !p) { authError('Remplissez tous les champs'); return; }
  if (p.length < 8) { authError('Mot de passe trop court (min 8 car.)'); return; }
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
