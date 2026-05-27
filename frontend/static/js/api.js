// ─── API token + fetch wrapper ────────────────────────────────────────────────
let _token = localStorage.getItem('hygie_token') || '';
function setToken(t) { _token = t; localStorage.setItem('hygie_token', t); }
function clearToken() { _token = ''; localStorage.removeItem('hygie_token'); }

async function api(url, method = 'GET', body = null) {
  const headers = { 'Content-Type': 'application/json' };
  if (_token) headers['Authorization'] = `Bearer ${_token}`;
  const opts = { method, headers };
  if (body) opts.body = JSON.stringify(body);
  // Normalize URL: strip trailing slash before query string to avoid 307 redirects
  const qIdx = url.indexOf('?');
  if (qIdx === -1) { url = url.replace(/\/+$/, ''); }
  else { url = url.slice(0, qIdx).replace(/\/+$/, '') + url.slice(qIdx); }
  const r = await fetch(url, opts);
  if (r.status === 401) { showLoginScreen(); throw new Error('Unauthorized'); }
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  if (r.status === 204) return null;
  return r.json();
}
