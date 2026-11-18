import type { TFunction } from 'i18next'
import { ApiError } from '../api/client'

/** Message for an error in the reader's language: the API's stable error code is the key. */
export function errorMessage(error: unknown, t: TFunction): string {
  if (error instanceof ApiError) {
    const key = `errors.${error.code}`
    const translated = t(key)
    return translated !== key ? translated : error.message
  }
  if (error instanceof TypeError) return t('errors.network')
  return t('errors.generic')
}
