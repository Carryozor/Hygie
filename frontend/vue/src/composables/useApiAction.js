// frontend/vue/src/composables/useApiAction.js
/**
 * Shared handler for destructive/mutating actions triggered from a confirm
 * modal (queue ignore/delete/purge, ignored requeue/remove, rule delete…).
 *
 * Before this composable, each view called the API directly with no
 * try/catch: `api/errorHandler.js` only toasts 422/429/5xx, so a 403/404
 * (or any other unhandled status) failed silently — the modal closed (or,
 * for doPurge, closed BEFORE the await even resolved) and the user believed
 * the action had succeeded.
 *
 * Contract: `onSuccess` (closing the modal / clearing the target ref) only
 * runs when `action` resolves. `reload` runs in both cases, since the
 * underlying data can be stale either way (e.g. the item was deleted by
 * something else, producing the very 404 we're reporting).
 */
import { emitError, formatApiError } from '@/api/errorHandler'

// Statuses installErrorInterceptor() (api/errorHandler.js) already toasts —
// reporting them again here would double the toast.
function isAlreadyToastedByInterceptor(status) {
  return status === 422 || status === 429 || (status >= 500 && status < 600)
}

export function reportActionError(err, label) {
  const status = err?.response?.status
  if (status && isAlreadyToastedByInterceptor(status)) return
  const detail = formatApiError(err)
  emitError(label ? `${label} — ${detail}` : detail)
}

export async function runConfirmedAction({ action, onSuccess, reload, errorLabel }) {
  try {
    await action()
    onSuccess?.()
    return true
  } catch (err) {
    reportActionError(err, errorLabel)
    return false
  } finally {
    if (reload) await reload()
  }
}
