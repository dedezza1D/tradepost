const brl = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' })

export const formatMoney = (cents: number): string => brl.format(cents / 100)

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
