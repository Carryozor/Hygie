// frontend/vue/src/api/errorHandler.js
/**
 * Format Axios errors into human-readable French messages.
 * Emits 'hygie:error' CustomEvent on window for ToastNotification.
 */

export function formatApiError(err) {
  if (!err.response) return 'Erreur réseau — vérifiez la connexion'
  const { status, data } = err.response

  if (status === 422 && data?.detail) {
    if (Array.isArray(data.detail)) {
      return data.detail
        .map(e => {
          const field = e.loc?.filter(l => l !== 'body').join('.') || ''
          return field ? `${field} : ${e.msg}` : e.msg
        })
        .join(' | ')
    }
    return String(data.detail)
  }

  if (status === 500) return data?.detail || 'Erreur serveur interne'
  if (status === 404) return 'Ressource introuvable'
  if (status === 403) return 'Accès refusé'
  if (status === 429) return 'Trop de requêtes — réessayez dans un instant'
  return `Erreur ${status}`
}

export function emitError(message, type = 'error') {
  window.dispatchEvent(new CustomEvent('hygie:error', {
    detail: { message, type }
  }))
}

export function installErrorInterceptor(axiosInstance) {
  axiosInstance.interceptors.response.use(
    r => r,
    err => {
      const status = err.response?.status
      // 401 handled by auth interceptor — skip
      if (status === 401) return Promise.reject(err)
      // Surface 422, 429, and 5xx to the user via toast
      if (status === 422 || status === 429 || (status >= 500 && status < 600)) {
        emitError(formatApiError(err))
      }
      return Promise.reject(err)
    }
  )
}
