/**
 * Hygie i18n — multi-language support
 * FR is the source language; keys are French strings.
 * Other languages are loaded from /static/i18n/{lang}.json on demand.
 */

const SUPPORTED_LANGS = {
  fr: 'Français',
  en: 'English',
  es: 'Español',
  de: 'Deutsch',
  it: 'Italiano',
  pl: 'Polski',
};

let _lang = 'fr';
let _dict = {};

// Detect language: localStorage > browser > fr
try {
  const saved = localStorage.getItem('hygie_lang');
  if (saved && SUPPORTED_LANGS[saved]) {
    _lang = saved;
  } else {
    const browserLang = (navigator.language || 'fr').toLowerCase().split('-')[0];
    _lang = SUPPORTED_LANGS[browserLang] ? browserLang : 'fr';
  }
} catch(e) { _lang = 'fr'; }

async function _loadDict(lang) {
  if (lang === 'fr') { _dict = {}; return; }
  try {
    const r = await fetch(`/static/i18n/${lang}.json`);
    _dict = r.ok ? await r.json() : {};
  } catch { _dict = {}; }
}

function t(key) {
  if (_lang === 'fr') return key;
  return _dict[key] || key;
}

const SKIP_CONTAINERS = ['#log-content', '#log-box'];

function _isInSkipContainer(el) {
  for (const sel of SKIP_CONTAINERS) {
    const c = document.querySelector(sel);
    if (c && c.contains(el)) return true;
  }
  return false;
}

function applyTranslations() {
  if (_lang === 'fr') return;
  const dict = _dict;

  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
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
    if (key && key.length > 2 && dict[key] !== undefined) {
      n.textContent = n.textContent.split(key).join(dict[key]);
    }
  });

  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (dict[key]) el.textContent = dict[key];
  });

  document.querySelectorAll('input[placeholder], textarea[placeholder]').forEach(el => {
    if (_isInSkipContainer(el)) return;
    const ph = el.getAttribute('placeholder');
    if (ph && dict[ph]) el.setAttribute('placeholder', dict[ph]);
  });

  document.querySelectorAll('[title]').forEach(el => {
    const tv = el.getAttribute('title');
    if (tv && dict[tv]) el.setAttribute('title', dict[tv]);
  });

  document.querySelectorAll('select option').forEach(el => {
    if (_isInSkipContainer(el)) return;
    const key = el.textContent.trim();
    if (key && dict[key]) el.textContent = dict[key];
  });
}

function _updateLangSelector() {
  const sel = document.getElementById('lang-selector');
  if (sel) sel.value = _lang;
}

function setLang(lang) {
  if (!SUPPORTED_LANGS[lang]) return;
  _lang = lang;
  try { localStorage.setItem('hygie_lang', lang); } catch(e) {}
  try {
    const tok = localStorage.getItem('hygie_token');
    if (tok) fetch('/api/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + tok},
      body: JSON.stringify({ui_language: lang})
    }).catch(() => {});
  } catch(e) {}
  location.reload();
}

// Load dict before DOMContentLoaded so t() works synchronously on first render
const _i18nReady = _loadDict(_lang);

document.addEventListener('DOMContentLoaded', async () => {
  await _i18nReady;
  _updateLangSelector();
  if (_lang !== 'fr') applyTranslations();
});

window.addEventListener('load', () => {
  if (typeof showPage !== 'function') return;
  const _orig = showPage;
  window.showPage = function(...args) {
    _orig.apply(this, args);
    if (_lang !== 'fr') setTimeout(applyTranslations, 300);
  };
});
