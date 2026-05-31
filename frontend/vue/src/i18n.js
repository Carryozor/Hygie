// src/i18n.js
import { createI18n } from 'vue-i18n'
import fr from './locales/fr.json'
import en from './locales/en.json'
import de from './locales/de.json'
import es from './locales/es.json'
import it from './locales/it.json'
import pt from './locales/pt.json'
import nl from './locales/nl.json'
import pl from './locales/pl.json'

export const SUPPORTED_LOCALES = ['fr', 'en', 'de', 'es', 'it', 'pt', 'nl', 'pl']

export const i18n = createI18n({
  legacy: false,
  locale: localStorage.getItem('hygie_lang') || 'fr',
  fallbackLocale: 'fr',
  messages: { fr, en, de, es, it, pt, nl, pl },
})

export function setLocale(lang) {
  if (!SUPPORTED_LOCALES.includes(lang)) return
  i18n.global.locale.value = lang
  localStorage.setItem('hygie_lang', lang)
  document.documentElement.setAttribute('lang', lang)
}
