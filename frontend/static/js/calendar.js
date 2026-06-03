// ─── Calendar ─────────────────────────────────────────────────────────────────
let _calEvents = {};
let _calYear = new Date().getFullYear();
let _calMonth = new Date().getMonth();
let _calSelected = null;

async function loadCalendar() {
  try {
    const data = await api('/api/calendar');
    _calEvents = data.events || {};
    renderCalendar();
  } catch(e) { toast('Erreur calendrier','error'); }
}

function calPrevMonth() { _calMonth--; if(_calMonth<0){_calMonth=11;_calYear--;} renderCalendar(); }
function calNextMonth() { _calMonth++; if(_calMonth>11){_calMonth=0;_calYear++;} renderCalendar(); }
function calGoToday() { const n=new Date(); _calYear=n.getFullYear(); _calMonth=n.getMonth(); _calSelected=null; renderCalendar(); }

function renderCalendar() {
  document.getElementById('cal-title').textContent = (() => {
    const mn = new Date(_calYear, _calMonth, 1).toLocaleString(_lang === 'en' ? 'en-US' : 'fr-FR', {month:'long'});
    return mn.charAt(0).toUpperCase() + mn.slice(1) + ' ' + _calYear;
  })();

  const firstDay = new Date(_calYear, _calMonth, 1);
  const daysInMonth = new Date(_calYear, _calMonth+1, 0).getDate();
  let startDow = (firstDay.getDay()+6)%7;
  const today = new Date().toISOString().slice(0,10);

  let cells = '';
  for(let i=0;i<startDow;i++) cells += `<div style="min-height:70px"></div>`;
  for(let d=1;d<=daysInMonth;d++) {
    const dateStr = `${_calYear}-${String(_calMonth+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const events = _calEvents[dateStr] || [];
    const isToday = dateStr===today;
    const isSelected = dateStr===_calSelected;
    const hasEvents = events.length>0;
    cells += `<div onclick="selectCalDay('${dateStr}')" style="
      min-height:70px;padding:6px;border-radius:8px;cursor:pointer;
      background:${isSelected?'#6366f130':hasEvents?'#f59e0b08':'#ffffff05'};
      border:1px solid ${isSelected?'var(--accent)':isToday?'#6366f160':hasEvents?'#f59e0b30':'var(--border)'};
      transition:all .15s
    ">
      <div style="font-size:13px;font-weight:${isToday?'700':'500'};color:${isToday?'var(--accent2)':'var(--text)'};margin-bottom:4px">${d}</div>
      ${events.length ? `<div style="display:flex;flex-direction:column;gap:2px">
        ${events.slice(0,3).map(e=>`<div style="font-size:10px;background:#f59e0b20;color:#f59e0b;border-radius:3px;padding:1px 4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${e.media_type==='Movie'?'🎬':'📺'} ${e.title}</div>`).join('')}
        ${events.length>3?`<div style="font-size:10px;color:var(--muted)">+${events.length-3} autre(s)</div>`:''}
      </div>` : ''}
    </div>`;
  }

  document.getElementById('cal-grid').innerHTML = cells;
  if (_calSelected) renderCalDetail(_calSelected);
}

function selectCalDay(dateStr) {
  _calSelected = _calSelected===dateStr ? null : dateStr;
  renderCalendar();
}

function renderCalDetail(dateStr) {
  const events = _calEvents[dateStr] || [];
  const box = document.getElementById('cal-detail');
  if (!events.length) { box.innerHTML=''; return; }
  const d = new Date(dateStr+'T12:00:00');
  const label = d.toLocaleDateString('fr-FR',{weekday:'long',day:'numeric',month:'long',year:'numeric'});
  box.innerHTML = `
    <div class="card" style="padding:20px">
      <div style="font-weight:600;color:#e2e8f0;margin-bottom:14px;display:flex;align-items:center;gap:8px">
        <i class="fas fa-calendar-day" style="color:#f59e0b"></i>${label} — ${events.length} suppression(s)
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px">
        ${events.map(e => {
          const poster = e.poster_url ? `<img src="${escapeHtml(proxyImg(e.poster_url))}" style="width:50px;height:75px;object-fit:cover;border-radius:4px;flex-shrink:0" onerror="this.style.display='none'">` : `<div style="width:50px;height:75px;background:#ffffff08;border-radius:4px;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:18px">${e.media_type==='Movie'?'🎬':'📺'}</div>`;
          const title = e.seerr_request_url
            ? `<a href="${escapeHtml(e.seerr_request_url)}" target="_blank" style="color:#e2e8f0;font-weight:500;font-size:12px;text-decoration:none">${escapeHtml(e.title)}</a>`
            : `<span style="color:#e2e8f0;font-weight:500;font-size:12px">${escapeHtml(e.title)}</span>`;
          const lib = escapeHtml(e.library_name || '');
          const req = e.seerr_username ? `<div style="font-size:11px;color:var(--muted)">👤 ${escapeHtml(e.seerr_username)}</div>` : '';
          return `<div style="display:flex;gap:10px;padding:10px;background:#ffffff05;border-radius:8px;border:1px solid var(--border)">
            ${poster}
            <div style="min-width:0;flex:1">
              ${title}
              <div style="font-size:11px;color:var(--muted);margin-top:3px">📚 ${lib}</div>
              ${req}
            </div>
          </div>`;
        }).join('')}
      </div>
    </div>`;
}
