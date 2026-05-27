// ─── Entry point — auto-refresh + bootstrap ───────────────────────────────────
setInterval(() => {
  if (document.visibilityState !== 'visible') return;
  if (currentPage === 'logs') loadLogs();
  if (currentPage === 'jobs') loadJobs();
  loadSchedulerInfo();
}, 15000);

initAuth();
