/**
 * Hygie i18n — FR / EN
 * Passif : ne modifie aucune fonction de app.js.
 */

const TRANSLATIONS = {
  en: {
    // ── Navigation ──────────────────────────────────────────────────────────
    'Tableau de bord': 'Dashboard',
    'Calendrier': 'Calendar',
    "File d'attente": 'Queue',
    'Bibliothèques': 'Libraries',
    'Paramètres': 'Settings',
    'Logs': 'Logs',
    'Historique jobs': 'Job History',
    'Stockage': 'Storage',
    'Ignorés': 'Ignored',

    // ── Dashboard ────────────────────────────────────────────────────────────
    'Scanner': 'Scan',
    'Vérifier suppressions': 'Check deletions',
    'Vérifier': 'Check',
    'En attente': 'Pending',
    'Supprimés (session)': 'Deleted (session)',
    'Exclus': 'Excluded',
    'Erreurs': 'Errors',
    'Statistiques globales (depuis le début)': 'Global statistics (all time)',
    'Suppressions totales': 'Total deletions',
    'Médias ignorés': 'Ignored media',
    'Scans effectués': 'Scans run',
    '12 derniers mois': 'Last 12 months',
    'Prochaines suppressions': 'Upcoming deletions',
    'Aucun média en attente': 'No media pending deletion',
    'Imminent': 'Imminent',

    // ── Queue ────────────────────────────────────────────────────────────────
    'Tous': 'All',
    '⏳ En attente': '⏳ Pending',
    '✅ Supprimés': '✅ Deleted',
    '❌ Erreurs': '❌ Errors',
    'Vue grille': 'Grid view',
    'Vue liste': 'List view',
    'Purger': 'Purge',
    'Titre': 'Title',
    'Bibliothèque': 'Library',
    'Type': 'Type',
    'Ajouté le': 'Added',
    'Vu le': 'Last watched',
    'Suppression': 'Deletion',
    'Demandé par': 'Requested by',
    'Statut': 'Status',
    'Actions': 'Actions',
    'Aucun résultat': 'No results',
    'Chargement...': 'Loading...',
    'Film': 'Movie',
    'Épisode': 'Episode',
    'Supprimé': 'Deleted',
    'Supprimée': 'Deleted',
    'Supprimés': 'Deleted',

    // ── Queue buttons ────────────────────────────────────────────────────────
    'Supprimer maintenant ?': 'Delete now?',
    'Supprimer ?': 'Delete?',
    'Supprimer toutes les entrées "deleted" de la file ?': 'Remove all "deleted" entries from the queue?',
    'entrée(s) purgée(s)': 'entry/entries purged',
    'Purgé': 'Purged',
    'Ignorer définitivement': 'Ignore permanently',

    // ── Ignore modal ─────────────────────────────────────────────────────────
    'Ignorer le média': 'Ignore media',
    'Raison (optionnelle)': 'Reason (optional)',
    'Expiration automatique': 'Auto-expiry',
    'jours (0 ou vide = permanent)': 'days (0 or blank = permanent)',
    'Ignorer': 'Ignore',
    'Annuler': 'Cancel',
    'Ignoré définitivement': 'Permanently ignored',
    'Ignoré le': 'Ignored on',
    'Expire le': 'Expires on',
    "Aucun média ignoré.": 'No ignored media.',
    "Aucun média ignoré": 'No ignored media',
    "Aucun média": 'No media',
    'Remettre': 'Restore',
    'Médias ignorés': 'Ignored Media',

    // ── Libraries ────────────────────────────────────────────────────────────
    'Nouvelle bibliothèque': 'New library',
    'Ajouter une bibliothèque': 'Add library',
    'Modifier la bibliothèque': 'Edit library',
    'Aucune bibliothèque.': 'No libraries configured.',
    'Nom': 'Name',
    'Bibliothèque Emby': 'Emby Library',
    'Conditions': 'Conditions',
    'Délai (jours)': 'Grace (days)',
    'Délai (j)': 'Grace (d)',
    'Délai de grâce (jours)': 'Grace period (days)',
    'Activée': 'Enabled',
    'Désactivée': 'Disabled',
    'Bibliothèque activée': 'Library enabled',
    'Bibliothèque désactivée': 'Library disabled',
    'Bibliothèque ajoutée': 'Library added',
    'Clonée': 'Cloned',
    'Entre détection et suppression': 'Between detection and deletion',
    'Logique des conditions': 'Condition logic',
    'Ajouter': 'Add',
    'Aucune condition': 'No conditions',
    'Cliquez sur "Ajouter" pour créer une condition': 'Click "Add" to create a condition',
    'Ajouté depuis': 'Added since',
    'Ajouté depuis (jours)': 'Added since (days)',
    'Jamais regardé': 'Never watched',
    'Pas regardé depuis (jours)': 'Not watched since (days)',
    'Nombre de lectures': 'Play count',
    'Filtres par utilisateur Seerr': 'Seerr user filters',
    'Inclure ou exclure certains utilisateurs Seerr de cette règle': 'Include or exclude specific Seerr users from this rule',
    'Scan démarré': 'Scan started',
    'Un scan est déjà en cours': 'A scan is already running',

    // ── Settings ─────────────────────────────────────────────────────────────
    'Général': 'General',
    'Enregistrer': 'Save',
    'Paramètres enregistrés': 'Settings saved',
    'Erreur paramètres': 'Settings error',
    'Mode Dry Run (simulation — aucune suppression réelle)': 'Dry Run mode (simulation — no actual deletions)',
    'Mode Dry Run actif': 'Dry Run mode active',
    'Scan toutes les (h)': 'Scan interval (h)',
    'Vérification suppressions toutes les (h)': 'Deletion check interval (h)',
    'Rétention historique supprimés (jours, 0=infini)': 'Deleted history retention (days, 0=forever)',
    'Niveau de log': 'Log level',
    'DEBUG — tout enregistrer': 'DEBUG — log everything',
    'INFO — actions & tâches': 'INFO — actions & tasks',
    'WARN — avertissements': 'WARN — warnings',
    'ERROR — erreurs uniquement': 'ERROR — errors only',
    'URL interne': 'Internal URL',
    'URL externe': 'External URL',
    'Clé API': 'API Key',
    'Mot de passe': 'Password',
    'Utilisateur': 'Username',
    'Collection "Bientôt supprimé"': 'Leaving Soon collection',
    'Nom de la collection (vide = désactivé)': 'Collection name (empty = disabled)',
    'Jours avant suppression dans la collection': 'Days before deletion to include',
    'Overlay sur les affiches': 'Overlay on posters',
    'Ajoute "Supprimé dans Xj" en haut de l\'affiche Emby': 'Adds "Deleted in Xd" banner on Emby poster',
    'Action sur le torrent': 'Torrent action',
    'Nom du tag (si "Ajouter un tag")': 'Tag name (if "Add a tag")',
    'Ajouter un tag': 'Add a tag',
    ': le fichier reste seedé, aucun orphelin possible.': ': file stays seeded, no orphan possible.',
    '🗑️ Supprimer torrent + fichier — qBit supprime tout (deleteFiles=true)': '🗑️ Delete torrent + file — qBit removes everything (deleteFiles=true)',
    'Tester': 'Test',
    'Sync collection': 'Sync collection',
    'Mentions Discord': 'Discord Mentions',
    'Associez un ID Discord à chaque utilisateur Seerr pour les mentions.': 'Link a Discord ID to each Seerr user for @mentions.',
    'Cliquez sur actualiser pour charger les utilisateurs Seerr': 'Click refresh to load Seerr users',
    'ID Discord': 'Discord ID',
    'Sauvegarder': 'Save',
    'Changer le mot de passe': 'Change password',
    'Mot de passe actuel': 'Current password',
    'Nouveau mot de passe': 'New password',
    'Confirmer': 'Confirm',
    'Mise à jour': 'Updated',

    // ── Logs ─────────────────────────────────────────────────────────────────
    'Effacer': 'Clear',
    'Logs vidés': 'Logs cleared',
    'Tous niveaux': 'All levels',
    'Toutes catégories': 'All categories',
    'Toutes sources': 'All sources',
    'Tâches': 'Tasks',
    'Système': 'System',
    'Aucun log': 'No logs',

    // ── Jobs ─────────────────────────────────────────────────────────────────
    'Historique des jobs': 'Job History',
    'Scan bibliothèques': 'Library scan',
    'Vérification suppressions': 'Deletion check',
    'Scan': 'Scan',
    'Vérification': 'Check',
    'Démarré': 'Started',
    'Terminé': 'Finished',
    'Durée': 'Duration',
    'Résultat': 'Result',
    'Aucun historique': 'No history',
    'Lancer un scan': 'Run scan',
    'Lancer une vérification': 'Run check',
    'Vérification lancée': 'Check started',
    'Une vérification est déjà en cours': 'A check is already running',

    // ── Storage ──────────────────────────────────────────────────────────────
    'Actualiser': 'Refresh',
    'Disques': 'Disks',
    'Espace récupérable': 'Reclaimable space',
    'Espace utilisé': 'Used space',
    'Total médias': 'Total media',
    'Films + Séries sur disque': 'Movies + Series on disk',
    'File de suppression': 'Deletion queue',
    'Données disque non disponibles — vérifiez la configuration Radarr/Sonarr': 'Disk data unavailable — check Radarr/Sonarr configuration',
    'Films non surveillés': 'Unmonitored movies',
    'Séries non surveillées': 'Unmonitored series',
    'Espace occupé': 'Space used',
    'Surveillés': 'Monitored',
    'Non surveillés': 'Unmonitored',
    'Séries au total': 'Total series',
    'Épisodes (fichiers)': 'Episodes (files)',

    // ── Calendar ─────────────────────────────────────────────────────────────
    'Aucune suppression prévue ce mois': 'No deletions scheduled this month',
    'Aujourd\'hui': 'Today',
    'Janvier': 'January', 'Février': 'February', 'Mars': 'March',
    'Avril': 'April', 'Mai': 'May', 'Juin': 'June',
    'Juillet': 'July', 'Août': 'August', 'Septembre': 'September',
    'Octobre': 'October', 'Novembre': 'November', 'Décembre': 'December',
    'Dim': 'Sun', 'Lun': 'Mon', 'Mar': 'Tue', 'Mer': 'Wed',
    'Jeu': 'Thu', 'Ven': 'Fri', 'Sam': 'Sat',
    '‹ Préc.': '‹ Prev',
    'Suiv. ›': 'Next ›',

    // ── Auth ─────────────────────────────────────────────────────────────────
    'Connexion': 'Sign in',
    'Connectez-vous à Hygie': 'Sign in to Hygie',
    "Nom d'utilisateur": 'Username',
    'Se connecter': 'Sign in',
    'Créer le compte': 'Create account',
    'Déconnexion': 'Sign out',

    // ── Misc / toasts ────────────────────────────────────────────────────────
    'Erreur': 'Error',
    'Erreur connexion': 'Connection error',
    'Erreur réseau': 'Network error',
    'Erreur suppression': 'Deletion error',
    'Erreur sync collection': 'Collection sync error',
    'Erreur sauvegarde': 'Save error',
    'Erreur régénération': 'Regeneration error',
    'Connecté !': 'Signed in!',
    'Collection Emby synchronisée': 'Emby collection synced',
    'Sync Seerr lancé...': 'Seerr sync started...',
    'Sync Seerr démarré — rechargez dans quelques secondes': 'Seerr sync started — reload in a few seconds',
    'Régénération des affiches...': 'Regenerating posters...',
    'Affiches en cours de régénération (quelques minutes)': 'Posters regenerating (a few minutes)',
    'Fermer': 'Close',
    'Modifier': 'Edit',
    'Supprimer': 'Delete',
    'Cloner': 'Clone',
    'Lancer': 'Run',
    'Enregistrer': 'Save',
    'Soutenir Hygie': 'Support Hygie',
    '· Désactivé': '· Disabled',
    'Fichier présent': 'File present',
    'Pas de fichier': 'No file',
    'auto-détecté': 'auto-detected',
    'Mise à jour': 'Updated',

    // ── Settings labels (seen in screenshots) ─────────────────────────────
    'URL interne (Docker)': 'Internal URL (Docker)',
    'URL externe (pour les affiches dans le navigateur)': 'External URL (for posters in browser)',
    'Si renseignée, les affiches sont chargées directement depuis cette URL (plus fiable que le proxy interne). Doit être accessible depuis ton navigateur.': 'If set, posters are loaded directly from this URL (more reliable than the internal proxy). Must be accessible from your browser.',
    'Leaving Soon collection': 'Leaving Soon collection',
    'Collection name (empty = disabled)': 'Collection name (empty = disabled)',
    'Grace (days)': 'Grace (days)',
    'Bandeau sur les affiches': 'Poster overlay',
    'Adds "Deleted in Xd" banner on Emby poster': 'Adds "Deleted in Xd" banner on Emby poster',
    "Action lors d'une suppression Hygie": 'Action on Hygie deletion',
    'Torrent action': 'Torrent action',
    '🏷️ Tag uniquement — torrent reste dans qBit, fichier continue de seeder': '🏷️ Tag only — torrent stays in qBit, file keeps seeding',
    'Tag : file stays seeded. No orphan possible.': 'Tag: file stays seeded. No orphan possible.',
    'Delete : qBittorrent efface le torrent ET le fichier physique. Plus de fichier orphelin.': 'Delete: qBittorrent removes the torrent AND the physical file. No orphan file.',
    'Tag name (if "Add a tag")': 'Tag name (if "Add a tag")',
    'URL interne (API)': 'Internal URL (API)',
    'URL externe (liens cliquables)': 'External URL (clickable links)',
    "Utilisée pour les liens dans la file d'attente. Peut être différente de l'URL interne.": 'Used for links in the queue. Can differ from internal URL.',
    'Webhook URL': 'Webhook URL',
    'Mentions utilisateurs': 'User mentions',
    'Associez chaque utilisateur Seerr à son ID Discord pour les mentions dans les notifications.': 'Link each Seerr user to their Discord ID for notification mentions.',
    'Click refresh to load Seerr users': 'Click refresh to load Seerr users',
    'Simule les suppressions sans rien effacer': 'Simulates deletions without actually removing anything',
    'DEBUG — tout logger': 'DEBUG — log everything',
    'Scan Interval (h)': 'Scan interval (h)',
    'Deletion check Interval (h)': 'Deletion check interval (h)',

    // ── Library modal labels ───────────────────────────────────────────────
    'ET — toutes les conditions': 'AND — all conditions',
    'OU — au moins une condition': 'OR — at least one condition',
    'Logique': 'Logic',
    'Ajouté depuis (jours)': 'Added since (days)',
    'Non vu depuis (jours)': 'Not watched since (days)',
    '> supérieur': '> greater than',
    '>= sup. ou égal': '>= greater or equal',
    '< inférieur': '< less than',
    '<= inf. ou égal': '<= less or equal',
    '= égal': '= equal',
    'Inclure uniquement': 'Include only',
    'Exclure': 'Exclude',
    'Un média est mis en file si la combinaison de conditions est satisfaite, puis supprimé après le délai de grâce.': 'A media item is queued when conditions are met, then deleted after the grace period.',

    // ── Library card ──────────────────────────────────────────────────────
    'Délai de grâce :': 'Grace period:',
    '11 filtre(s) Seerr': '11 Seerr filter(s)',
    'filtre(s) Seerr': 'Seerr filter(s)',
    'Désactiver': 'Disable',
    'Activer': 'Enable',

    // ── Logs ─────────────────────────────────────────────────────────────
    'Ignoré (non demandé sur Seerr)': 'Ignored (not requested on Seerr)',
    'Scan terminé —': 'Scan complete —',
    'média(s) ajouté(s)': 'media added',
    'ajouté(s)': 'added',
    'démarré': 'started',

    // ── Storage labels ────────────────────────────────────────────────────
    'Total dans la bibliothèque': 'Total in library',
    'Avec fichier': 'With file',
    'Surveillés': 'Monitored',
    'Non surveillés': 'Unmonitored',
    'Espace utilisé': 'Space used',
    'Taille moyenne / film': 'Avg size / movie',
    'Taille moyenne / série': 'Avg size / series',
    'Total médias': 'Total media',
    'Films + Séries sur disque': 'Movies + Series on disk',
    'File de suppression': 'Deletion queue',
    'Voir la file': 'View queue',
    'si les': 'if the',
    'médias en attente sont supprimés': 'pending media are deleted',

    // ── Calendar ─────────────────────────────────────────────────────────
    'Calendrier des suppressions': 'Deletion calendar',

    // ── Ignored page ─────────────────────────────────────────────────────
    'Ces médias ne seront jamais ajoutés automatiquement à la file d\'attente, même s\'ils remplissent les conditions.': 'These media items will never be automatically added to the queue, even if they meet the conditions.',
    'Utilisez le bouton': 'Use the',
    'dans la file d\'attente pour ignorer définitivement un média.': 'button in the queue to permanently ignore a media item.',
    'No ignored media.': 'No ignored media.',

    // ── Unmonitored ───────────────────────────────────────────────────────
    'Fichier présent': 'File present',
    'Pas de fichier': 'No file',
    'auto-détecté': 'auto-detected',

    // ── Library modal — exact strings from screenshot ─────────────────────
    'Un média est mis en file si la combinaison de conditions est satisfaite, puis supprimé après le délai de grâce.': 'A media item is queued when all conditions are met, then deleted after the grace period.',
    'Délai de grâce': 'Grace period',
    'Logique': 'Logic',
    'Conditions': 'Conditions',
    'Seerr user filters': 'Seerr user filters',
    'ET — toutes les conditions': 'AND — all conditions',
    'OU — au moins une condition': 'OR — at least one condition',

    // ── Storage page — exact strings from screenshot ───────────────────────
    'Utilisation disque': 'Disk usage',
    'libres': 'free',
    'total': 'total',
    'Total dans la bibliothèque': 'Total in library',
    'Avec fichier': 'With file',
    'Surveillés': 'Monitored',
    'Non surveillés': 'Unmonitored',
    'Espace utilisé': 'Space used',
    'Taille moyenne / film': 'Avg size / movie',
    'Séries au total': 'Total series',
    'Surveillées': 'Monitored',
    'Non surveillées': 'Unmonitored',
    'Épisodes (fichiers)': 'Episodes (files)',
    'Taille moyenne / série': 'Avg size / series',
    'Total médias': 'Total media',
    'Films + Séries sur disque': 'Movies + Series on disk',
    'File de suppression': 'Deletion queue',
    'En attente': 'Pending',
    'Supprimés': 'Deleted',
    'Exclus': 'Excluded',
    'Erreurs': 'Errors',
    'Voir la file': 'View queue',
    'si les': 'if the',
    'médias en attente sont supprimés': 'pending media are deleted',
    '💾 Espace récupérable': '💾 Reclaimable space',
    'lib_hint': 'A media item is queued when conditions are met, then deleted after the grace period.',
    // Storage section headers (context-specific to avoid colliding with library names)
    'Films (hdr)': 'Movies',
    'Séries (hdr)': 'Series',
    'Films (Storage)': 'Movies',
    'Utilisation disque': 'Disk usage',
  }
};

let _lang = 'en'; // Default to English
try {
  const saved = localStorage.getItem('hygie_lang');
  if (saved) {
    _lang = saved; // User preference takes priority
  } else {
    // Auto-detect from browser — use FR only if browser is explicitly French
    const browserLang = (navigator.language || navigator.userLanguage || 'en').toLowerCase();
    _lang = browserLang.startsWith('fr') ? 'fr' : 'en';
  }
} catch(e) { _lang = 'en'; }

function _updateBtn() {
  // Update active state on the two-button selector
  const btnFr = document.getElementById('lang-btn-fr');
  const btnEn = document.getElementById('lang-btn-en');
  if (btnFr) {
    btnFr.classList.toggle('active', _lang === 'fr');
    btnFr.style.background = _lang === 'fr' ? 'var(--accent)' : 'transparent';
    btnFr.style.color = _lang === 'fr' ? '#fff' : 'var(--muted)';
  }
  if (btnEn) {
    btnEn.classList.toggle('active', _lang === 'en');
    btnEn.style.background = _lang === 'en' ? 'var(--accent)' : 'transparent';
    btnEn.style.color = _lang === 'en' ? '#fff' : 'var(--muted)';
  }
}

// Elements that contain user-defined names — skip them to avoid translating library names etc.
const SKIP_CONTAINERS = [
  '#log-content', '#log-box',         // logs are server-generated, untranslatable
];

function _isInSkipContainer(el) {
  for (const sel of SKIP_CONTAINERS) {
    const container = document.querySelector(sel);
    if (container && container.contains(el)) return true;
  }
  return false;
}

function applyTranslations() {
  if (_lang === 'fr') return;
  const dict = TRANSLATIONS.en;

  // Walk text nodes — but skip log containers and long dynamic strings
  const walker = document.createTreeWalker(
    document.body, NodeFilter.SHOW_TEXT, null
  );
  const nodes = [];
  while (walker.nextNode()) {
    const n = walker.currentNode;
    const tag = n.parentElement && n.parentElement.tagName;
    if (tag === 'SCRIPT' || tag === 'STYLE') continue;
    if (_isInSkipContainer(n)) continue;
    nodes.push(n);
  }
  nodes.forEach(n => {
    const key = n.textContent.trim();
    // Only translate if EXACT match AND key is a UI string (not user data)
    // Skip very short keys (1-2 chars) and keys that look like user content
    if (key && key.length > 2 && dict[key] !== undefined) {
      n.textContent = n.textContent.split(key).join(dict[key]);
    }
  });

  // data-i18n attribute (key lookup)
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (dict[key]) el.textContent = dict[key];
  });

  // Placeholders
  document.querySelectorAll('input[placeholder], textarea[placeholder]').forEach(el => {
    if (_isInSkipContainer(el)) return;
    const ph = el.getAttribute('placeholder');
    if (ph && dict[ph]) el.setAttribute('placeholder', dict[ph]);
  });

  // Title attributes (tooltips)
  document.querySelectorAll('[title]').forEach(el => {
    const t = el.getAttribute('title');
    if (t && dict[t]) el.setAttribute('title', dict[t]);
  });

  // Select options — only in modal/settings (not in dynamic lists)
  document.querySelectorAll('select option').forEach(el => {
    if (_isInSkipContainer(el)) return;
    const key = el.textContent.trim();
    if (key && dict[key]) el.textContent = dict[key];
  });
}

function setLang(lang) {
  if (lang !== 'fr' && lang !== 'en') return;
  _lang = lang;
  try { localStorage.setItem('hygie_lang', lang); } catch(e) {}
  // Persist to backend (non-blocking)
  try {
    const tok = localStorage.getItem('hygie_token');
    if (tok) fetch('/api/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + tok},
      body: JSON.stringify({ui_language: lang})
    }).catch(() => {});
  } catch(e) {}
  _updateBtn();
  location.reload(); // Reload for clean translation state in both directions
}

function toggleLang() {
  setLang(_lang === 'fr' ? 'en' : 'fr');
}

// Init: update button text + apply if EN
document.addEventListener('DOMContentLoaded', () => {
  _updateBtn();
  if (_lang === 'en') setTimeout(applyTranslations, 400);
});

// Re-apply after page navigation — wait for app.js to define showPage first
window.addEventListener('load', () => {
  if (typeof showPage !== 'function') return;
  const _orig = showPage;
  window.showPage = function(...args) {
    _orig.apply(this, args);
    if (_lang === 'en') setTimeout(applyTranslations, 300);
  };
});
