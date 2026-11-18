import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMe } from '../api/auth'
import { api } from '../api/client'
import type { ShippingOption } from '../api/types'
import { formatMoney } from '../lib/format'
import { CityPicker, type City } from './CityPicker'

interface Quote {
  mode: 'fixed' | 'calculated'
  destination: string | null
  options: ShippingOption[]
}

/** Shipping options for a listing with calculated shipping, to a city the visitor picks. */
export function ShippingQuote({ listingId }: { listingId: string }) {
  const { t } = useTranslation()
  const { data: me } = useMe()
  const own = me?.municipality_id && me.city && me.state ? { ibge_code: me.municipality_id, name: me.city, state: me.state } : null
  const [city, setCity] = useState<City | null>(own)
  const quote = useQuery({
    queryKey: ['shipping', listingId, city?.ibge_code],
    queryFn: () => api<Quote>(`/api/listings/${listingId}/shipping?to=${city?.ibge_code}`),
    enabled: Boolean(city),
  })
  return (
    <section className="card space-y-3 p-4" aria-labelledby="shipping-title">
      <h2 id="shipping-title" className="font-semibold">{t('shipping.title')}</h2>
      <CityPicker key={city?.ibge_code ?? 'none'} value={city} onChange={setCity} label={t('shipping.to')} />
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
