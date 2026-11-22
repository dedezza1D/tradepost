import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../api/client'
import { currentLanguage } from '../i18n'

interface Rates {
  usd: { brl_per_unit: number; quoted_on: string } | null
}

// "US$", not "$": next to prices in R$ a bare dollar sign is ambiguous.
const whole = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })
const withCents = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

function useUsdRate(enabled: boolean) {
  return useQuery({
    queryKey: ['fx'],
    queryFn: () => api<Rates>('/api/fx'),
    enabled,
    staleTime: 60 * 60_000,
  }).data?.usd
}

/**
 * In English, the price converted to US dollars as an estimate (everything is charged in reais).
 * `detail` adds the rate and its date, for the listing page.
 */
export function ApproxPrice({ cents, detail = false, className = '' }: { cents: number; detail?: boolean; className?: string }) {
  const { t } = useTranslation()
  const english = currentLanguage() === 'en'
  const rate = useUsdRate(english)
  if (!english || !rate) return null
  const dollars = cents / 100 / rate.brl_per_unit
  const amount = `US$ ${dollars >= 100 ? whole.format(dollars) : withCents.format(dollars)}`
  const date = new Date(`${rate.quoted_on}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  return (
    <span className={`block text-muted ${className}`}>
      {detail
        ? t('price.approxDetail', { amount, rate: rate.brl_per_unit.toFixed(2), date })
        : t('price.approx', { amount })}
    </span>
  )
}
