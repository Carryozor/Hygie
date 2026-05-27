// ─── WebSocket ────────────────────────────────────────────────────────────────
let _ws = null;
let _wsReconnectTimer = null;

function initWebSocket() {
  if (_ws && (_ws.readyState === WebSocket.OPEN || _ws.readyState === WebSocket.CONNECTING)) return;
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  _ws = new WebSocket(`${proto}//${location.host}/ws`);

  _ws.onopen = () => {
    _ws.send(JSON.stringify({ token: _token }));
  };

  _ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === 'ping') { _ws.send('ping'); return; }
    if (msg.type === 'connected') {
      document.getElementById('ws-indicator').style.background = '#10b981';
      document.getElementById('ws-indicator').title = 'Temps réel connecté';
      return;
    }
    if (msg.type === 'log') {
      if (currentPage === 'logs') _prependLog(msg.data);
      if (currentPage === 'dashboard') loadDashboard();
    }
  };

  _ws.onclose = (event) => {
    document.getElementById('ws-indicator').style.background = '#ef4444';
    document.getElementById('ws-indicator').title = 'Temps réel déconnecté';
    if (event.code === 1008) {
      document.getElementById('ws-indicator').title = 'Session expirée — reconnectez-vous';
      return;
    }
    clearTimeout(_wsReconnectTimer);
    _wsReconnectTimer = setTimeout(initWebSocket, 3000);
  };

  _ws.onerror = () => {
    _ws.close();
  };
}

function _prependLog(log) {
  const box = document.getElementById('logs-container');
  if (!box) return;
  const ts = log.ts?new Date(log.ts).toLocaleString('fr-FR',{dateStyle:'short',timeStyle:'medium'}):'—';
  const row = document.createElement('div');
  row.className = `log-row log-${escapeHtml(log.level||'')}`;
  row.style.background = '#6366f118';
  const tsSpan = document.createElement('span');
  tsSpan.className = 'log-ts'; tsSpan.textContent = ts;
  const levelSpan = document.createElement('span');
  levelSpan.className = 'log-level'; levelSpan.textContent = log.level||'';
  const catSpan = document.createElement('span');
  catSpan.className = 'log-cat'; catSpan.textContent = log.source||'';
  const msgSpan = document.createElement('span');
  msgSpan.style.color = 'var(--text)'; msgSpan.textContent = log.message||'';
  row.append(tsSpan, levelSpan, catSpan, msgSpan);
  box.prepend(row);
  setTimeout(() => { row.style.background = ''; row.style.transition = 'background 1s'; }, 100);
  const rows = box.querySelectorAll('.log-row');
  if (rows.length > 300) rows[rows.length-1].remove();
}
