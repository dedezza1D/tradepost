import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../api/client'
import type { ShippingOption } from '../api/types'
import { useShipToField } from '../lib/city'
import { formatMoney } from '../lib/format'
import { CityPicker } from './CityPicker'

interface Quote {
  mode: 'fixed' | 'calculated'
  destination: string | null
  options: ShippingOption[]
}

/** Shipping options for a listing with calculated shipping, to a city the visitor picks. */
export function ShippingQuote({ listingId }: { listingId: string }) {
  const { t } = useTranslation()
  const { city, value, onChange } = useShipToField()
  const quote = useQuery({
    queryKey: ['shipping', listingId, city?.ibge_code],
    queryFn: () => api<Quote>(`/api/listings/${listingId}/shipping?to=${city?.ibge_code}`),
    enabled: Boolean(city),
  })
  return (
    <section className="card space-y-3 p-4" aria-labelledby="shipping-title">
      <h2 id="shipping-title" className="font-semibold">{t('shipping.title')}</h2>
      <CityPicker value={value} onChange={onChange} label={t('shipping.to')} />
      {quote.isFetching && <div className="skeleton h-12" />}
      {quote.data && quote.data.options.length > 0 && (
        <ul className="space-y-2" aria-live="polite">
          {quote.data.options.map((o) => (
            <li key={o.service} className="flex items-center justify-between gap-3 rounded-xl bg-surface-2 px-3 py-2 text-sm">
              <span>
                <span className="block font-semibold">{t(`shipping.service.${o.service}`)}</span>
                {o.days != null && <span className="text-xs text-muted">{t('shipping.days', { count: o.days })} · {o.carrier}</span>}
              </span>
              <span className="font-bold">{formatMoney(o.price_cents)}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-muted">{t('shipping.simulated')}</p>
    </section>
  )
}
