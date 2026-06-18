// frontend/vue/src/utils/safeUrl.js
/**
 * Validate a URL's scheme before binding it to :href.
 *
 * Vue does not auto-escape :href the way it does {{ }} text interpolation —
 * a `javascript:` URL in a server-controlled field (Seerr request URL,
 * media server ext_url, etc.) would execute in the authenticated origin.
 * Only http/https/mailto are allowed; anything else (including a malformed
 * or empty value) resolves to '#' so the link is present but inert.
 */
const ALLOWED_SCHEMES = ['http:', 'https:', 'mailto:']

export function safeUrl(url) {
  if (!url) return '#'
  try {
    const parsed = new URL(url, window.location.origin)
    return ALLOWED_SCHEMES.includes(parsed.protocol) ? url : '#'
  } catch {
    return '#'
  }
}
