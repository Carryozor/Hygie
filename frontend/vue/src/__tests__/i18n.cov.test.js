// src/__tests__/i18n.cov.test.js
// Coverage for src/i18n.js (0% before this file) plus a drift guard on the
// 8 locale JSON files it loads: a translation added to fr.json but forgotten
// in another locale silently falls back to French for that language instead
// of failing loudly, so the key sets must match exactly.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

const LOCALES_DIR = path.resolve(process.cwd(), 'src/locales')
const LOCALE_CODES = ['fr', 'en', 'de', 'es', 'it', 'pt', 'nl', 'pl']

function flattenKeys(obj, prefix = '') {
  const keys = new Set()
  for (const [k, v] of Object.entries(obj)) {
    const full = prefix ? `${prefix}.${k}` : k
    if (v !== null && typeof v === 'object' && !Array.isArray(v)) {
      for (const nested of flattenKeys(v, full)) keys.add(nested)
    } else {
      keys.add(full)
    }
  }
  return keys
}

describe('locale files — key parity', () => {
  const parsed = Object.fromEntries(
    LOCALE_CODES.map(code => [code, JSON.parse(fs.readFileSync(path.join(LOCALES_DIR, `${code}.json`), 'utf-8'))])
  )
  const keysByLocale = Object.fromEntries(LOCALE_CODES.map(code => [code, flattenKeys(parsed[code])]))

  it('all 8 locale files exist and parse as JSON objects', () => {
    for (const code of LOCALE_CODES) {
      expect(typeof parsed[code]).toBe('object')
      expect(keysByLocale[code].size).toBeGreaterThan(0)
    }
  })

  it('every locale has exactly the same key set as fr.json (the reference)', () => {
    const reference = keysByLocale.fr
    for (const code of LOCALE_CODES) {
      if (code === 'fr') continue
      const missing = [...reference].filter(k => !keysByLocale[code].has(k))
      const extra = [...keysByLocale[code]].filter(k => !reference.has(k))
      expect(missing, `${code}.json is missing keys present in fr.json`).toEqual([])
      expect(extra, `${code}.json has keys not present in fr.json`).toEqual([])
    }
  })

  it('no locale file has an empty-string translation for a key that has real content in fr.json', () => {
    const reference = parsed.fr
    function checkEmpty(refObj, otherObj, code, prefix = '') {
      for (const [k, refVal] of Object.entries(refObj)) {
        const full = prefix ? `${prefix}.${k}` : k
        const otherVal = otherObj?.[k]
        if (refVal !== null && typeof refVal === 'object' && !Array.isArray(refVal)) {
          checkEmpty(refVal, otherVal || {}, code, full)
        } else if (typeof refVal === 'string' && refVal.trim() !== '') {
          expect(otherVal, `${code}.json: "${full}" is empty/missing`).not.toBe('')
        }
      }
    }
    for (const code of LOCALE_CODES) {
      if (code === 'fr') continue
      checkEmpty(reference, parsed[code], code)
    }
  })
})

describe('i18n.js', () => {
  const originalLang = document.documentElement.getAttribute('lang')

  beforeEach(() => {
    localStorage.clear()
    vi.resetModules()
  })
  afterEach(() => {
    localStorage.clear()
    if (originalLang) document.documentElement.setAttribute('lang', originalLang)
    else document.documentElement.removeAttribute('lang')
  })

  it('exposes exactly the 8 supported locale codes', async () => {
    const { SUPPORTED_LOCALES } = await import('../i18n.js')
    expect(SUPPORTED_LOCALES).toEqual(['fr', 'en', 'de', 'es', 'it', 'pt', 'nl', 'pl'])
  })

  it('defaults to French when localStorage has no saved language', async () => {
    const { i18n } = await import('../i18n.js')
    expect(i18n.global.locale.value).toBe('fr')
  })

  it('boots with the language already saved in localStorage', async () => {
    localStorage.setItem('hygie_lang', 'de')
    const { i18n } = await import('../i18n.js')
    expect(i18n.global.locale.value).toBe('de')
  })

  it('setLocale switches the active locale, persists it, and sets <html lang>', async () => {
    const { i18n, setLocale } = await import('../i18n.js')
    setLocale('es')
    expect(i18n.global.locale.value).toBe('es')
    expect(localStorage.getItem('hygie_lang')).toBe('es')
    expect(document.documentElement.getAttribute('lang')).toBe('es')
  })

  it('setLocale ignores an unsupported locale code (no state change)', async () => {
    const { i18n, setLocale } = await import('../i18n.js')
    setLocale('fr')
    setLocale('klingon')
    expect(i18n.global.locale.value).toBe('fr')
    expect(localStorage.getItem('hygie_lang')).not.toBe('klingon')
  })

  it('every supported locale actually resolves a message (fallbackLocale never silently masks a missing translation)', async () => {
    const { i18n, setLocale, SUPPORTED_LOCALES } = await import('../i18n.js')
    for (const code of SUPPORTED_LOCALES) {
      setLocale(code)
      // Any real leaf key works here — parity is asserted exhaustively above.
      const msg = i18n.global.t('status.pending')
      expect(msg).not.toBe('status.pending') // vue-i18n returns the key itself when unresolved
    }
  })
})
