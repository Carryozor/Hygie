// src/utils/__tests__/safeUrl.test.js
//
// safeUrl() gates every :href built from server-controlled data (Seerr
// request URL, media server ext_url, etc.) against script-executing
// schemes. Vue does not auto-escape :href the way it escapes {{ }} text
// interpolation, so this is the only thing standing between a stored
// `javascript:` URL and code execution in the authenticated origin.
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { safeUrl } from '../safeUrl'

describe('safeUrl', () => {
  let originalHref
  beforeEach(() => {
    originalHref = window.location.href
  })
  afterEach(() => {
    // jsdom allows rewriting location between tests; nothing to restore since
    // we never navigate — kept for symmetry/documentation.
    void originalHref
  })

  describe('dangerous schemes are neutralized to "#"', () => {
    const dangerous = [
      'javascript:alert(1)',
      'JAVASCRIPT:alert(1)',
      'JavaScript:alert(1)',
      '  javascript:alert(1)',
      'javascript:alert(1)  ',
      'java\tscript:alert(1)',   // tab stripped anywhere by URL parsing
      'java\nscript:alert(1)',   // newline stripped anywhere by URL parsing
      '\u0000javascript:alert(1)',
      'javascript\t:alert(1)',
      'vbscript:msgbox(1)',
      'VBScript:msgbox(1)',
      'data:text/html,<script>alert(1)</script>',
      'data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==',
      'file:///etc/passwd',
      'blob:https://evil.example.com/uuid',
    ]
    it.each(dangerous)('blocks %s', input => {
      expect(safeUrl(input)).toBe('#')
    })
  })

  describe('encoded/obfuscated scheme attempts do not resolve to a script scheme', () => {
    it('percent-encoded "javascript" is not decoded into a live scheme', () => {
      const result = safeUrl('%6A%61%76%61script:alert(1)')
      // The browser URL parser never treats this as scheme "javascript:" —
      // '%' is not a valid scheme character, so it resolves as a same-origin
      // relative path (http/https), which is safe.
      expect(result).not.toBe('#')
      expect(result.startsWith('javascript:')).toBe(false)
    })

    it('HTML-entity-encoded scheme is inert (never parsed as HTML by :href binding)', () => {
      const result = safeUrl('javascript&colon;alert(1)')
      expect(result.startsWith('javascript:')).toBe(false)
    })
  })

  describe('allowed schemes pass through unchanged', () => {
    it('allows http', () => {
      expect(safeUrl('http://example.com/path')).toBe('http://example.com/path')
    })
    it('allows https', () => {
      expect(safeUrl('https://example.com/path?x=1')).toBe('https://example.com/path?x=1')
    })
    it('allows mailto', () => {
      expect(safeUrl('mailto:user@example.com')).toBe('mailto:user@example.com')
    })
  })

  describe('relative URLs pass through unchanged', () => {
    it('allows a root-relative path', () => {
      expect(safeUrl('/library/42')).toBe('/library/42')
    })
    it('allows a bare relative path', () => {
      expect(safeUrl('library/42')).toBe('library/42')
    })
  })

  describe('malformed / empty input falls back to "#"', () => {
    it('empty string', () => {
      expect(safeUrl('')).toBe('#')
    })
    it('null', () => {
      expect(safeUrl(null)).toBe('#')
    })
    it('undefined', () => {
      expect(safeUrl(undefined)).toBe('#')
    })
    it('a string the URL parser rejects outright', () => {
      expect(safeUrl('http://')).toBe('#')
    })
    it('a malformed authority', () => {
      expect(safeUrl('http://[invalid')).toBe('#')
    })
  })
})
