import { useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api, json } from '../api/client'
import { useMe } from '../api/auth'
import { LANGUAGES, currentLanguage, type Language } from '../i18n'

export function LanguageSwitcher() {
  const { t, i18n } = useTranslation()
  const { data: me } = useMe()
  const qc = useQueryClient()

  async function change(language: Language) {
    await i18n.changeLanguage(language)
    // Prices and dates are formatted outside React; refetching re-renders them.
    void qc.invalidateQueries()
    if (me && me.locale !== language) {
      try {
        await api('/api/auth/me', { method: 'PATCH', body: json({ locale: language }) })
      } catch {
        // The choice still applies in this browser.
      }
    }
  }

  return (
    <label className="flex items-center gap-1 text-sm text-stone-600">
      <span className="sr-only">{t('common.language')}</span>
      <span aria-hidden>🌐</span>
      <select
        className="rounded-md border-0 bg-transparent py-1 pr-6 text-sm font-medium focus:ring-2 focus:ring-brand-100"
        value={currentLanguage()}
        onChange={(e) => void change(e.target.value as Language)}
      >
        {LANGUAGES.map((l) => (
          <option key={l.code} value={l.code}>
            {l.code === 'pt-BR' ? 'PT' : 'EN'}
          </option>
        ))}
      </select>
    </label>
  )
}
