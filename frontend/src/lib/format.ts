import { currentLanguage } from '../i18n'

// Prices are always in BRL; only the formatting follows the interface language.
const formatters = new Map<string, Intl.NumberFormat>()

export function formatMoney(cents: number): string {
  const locale = currentLanguage() === 'en' ? 'en-US' : 'pt-BR'
  let formatter = formatters.get(locale)
  if (!formatter) {
    formatter = new Intl.NumberFormat(locale, { style: 'currency', currency: 'BRL' })
    formatters.set(locale, formatter)
  }
  return formatter.format(cents / 100)
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(currentLanguage() === 'en' ? 'en-US' : 'pt-BR', {
    dateStyle: 'short',
    timeStyle: 'short',
  })
}

/** Parses "1.234,56", "1234.56" or "1234" into cents; null when not a valid amount. */
export function parseMoney(input: string): number | null {
  const cleaned = input.trim().replace(/[^\d,.]/g, '')
  if (!cleaned) return null
  const lastSep = Math.max(cleaned.lastIndexOf(','), cleaned.lastIndexOf('.'))
  let normalized: string
  if (lastSep >= 0 && cleaned.length - lastSep - 1 <= 2) {
    normalized = cleaned.slice(0, lastSep).replace(/[.,]/g, '') + '.' + cleaned.slice(lastSep + 1)
  } else {
    normalized = cleaned.replace(/[.,]/g, '')
  }
  const value = Number(normalized)
  return Number.isFinite(value) ? Math.round(value * 100) : null
}

export const centsToInput = (cents: number): string => (cents / 100).toFixed(2).replace('.', ',')

export const conditionLabel = { new: 'New', used: 'Used' } as const

export const statusLabel = {
  draft: 'Draft',
  active: 'Active',
  paused: 'Paused',
  sold_out: 'Sold out',
  removed: 'Removed',
} as const
