// ─── Navigation ───────────────────────────────────────────────────────────────
let currentPage = 'dashboard';

function showPage(page) {
  document.querySelectorAll('.page').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
  document.getElementById(`page-${page}`)?.classList.add('active');
  document.getElementById(`nav-${page}`)?.classList.add('active');
  currentPage = page;
  const loaders = {
    dashboard: loadDashboard,
    queue: () => { queueOffset=0; loadQueue(); },
    libraries: loadLibraries,
    settings: loadSettings,
    logs: loadLogs,
    jobs: loadJobs,
    calendar: loadCalendar,
    storage: loadStorage,
    ignored: loadIgnored,
    unmonitored: loadUnmonitored,
  };
  if (loaders[page]) loaders[page]();
}
