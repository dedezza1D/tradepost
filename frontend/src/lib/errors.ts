import type { TFunction } from 'i18next'
import { ApiError } from '../api/client'

/** Message for an error in the reader's language: the API's stable error code is the key. */
export function errorMessage(error: unknown, t: TFunction): string {
  if (error instanceof ApiError) {
    const key = `errors.${error.code}`
    const translated = t(key)
    const message = translated !== key ? translated : error.message
    // A server-side failure carries the id of its log entry, so a report can be traced.
    if (error.status >= 500 && !error.unavailable && error.requestId) {
      return `${message} ${t('errors.reference', { id: error.requestId.slice(0, 8) })}`
    }
    return message
  }
  if (error instanceof TypeError) return t('errors.network')
  return t('errors.generic')
}
