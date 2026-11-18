import i18n from 'i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import { initReactI18next } from 'react-i18next'
import en from './locales/en.json'
import ptBR from './locales/pt-BR.json'

export const LANGUAGES = [
  { code: 'pt-BR', label: 'Português' },
  { code: 'en', label: 'English' },
] as const
export type Language = (typeof LANGUAGES)[number]['code']

const STORAGE_KEY = 'tp_lang'

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: { 'pt-BR': { translation: ptBR }, en: { translation: en } },
    supportedLngs: ['pt-BR', 'en'],
    fallbackLng: 'pt-BR',
    load: 'currentOnly',
    interpolation: { escapeValue: false },
    detection: {
      order: ['localStorage', 'navigator'],
      lookupLocalStorage: STORAGE_KEY,
      caches: ['localStorage'],
      // Browsers report regional variants ("en-US", "pt-PT"): map them to the two languages
      // the app has. Anything else falls back to Portuguese.
      convertDetectedLanguage: (lng: string) => (lng.toLowerCase().startsWith('en') ? 'en' : 'pt-BR'),
    },
  })

export function currentLanguage(): Language {
  return i18n.resolvedLanguage === 'en' ? 'en' : 'pt-BR'
}

i18n.on('languageChanged', () => {
  document.documentElement.lang = currentLanguage()
})
document.documentElement.lang = currentLanguage()

export default i18n
