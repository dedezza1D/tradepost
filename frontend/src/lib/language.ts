import { useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useMe } from '../api/auth'
import { api, json } from '../api/client'
import type { Me } from '../api/types'
import type { Language } from '../i18n'

/**
 * Switch the interface language. It applies at once in this browser (remembered there), and a
 * signed-in person's choice is also saved to the account so other devices follow it.
 */
export function useChangeLanguage() {
  const { i18n } = useTranslation()
  const { data: me } = useMe()
  const qc = useQueryClient()
  return async (language: Language) => {
    await i18n.changeLanguage(language)
    // Server-made texts (categories, notifications) come back in the new language.
    void qc.invalidateQueries()
    if (!me) return
    try {
      const updated = await api<Me>('/api/auth/me', { method: 'PATCH', body: json({ locale: language }) })
      qc.setQueryData(['me'], updated)
    } catch {
      // Applied in this browser anyway.
    }
  }
}
