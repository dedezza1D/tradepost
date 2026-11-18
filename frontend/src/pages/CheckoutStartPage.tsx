import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useMe } from '../api/auth'
import { ApiError, api, json } from '../api/client'
import { type Checkout, useProviders } from '../api/orders'
import { CityPicker, type City } from '../components/CityPicker'
import { errorMessage } from '../lib/errors'
import { formatMoney } from '../lib/format'
import { Icon } from '../ui/icons'
import { PageHeader } from '../ui/primitives'

interface Quote {
  orders: {
    seller_id: string
    items: { listing_id: string; variant_id: string | null; title: string; variant_name: string | null; quantity: number; unit_price_cents: number; shipping_cents: number }[]
    items_cents: number
    shipping_cents: number
    total_cents: number
    shipping_service: string | null
    shipping_days: number | null
  }[]
  total_cents: number
  destination: string | null
  shipping_by_service: Record<string, number>
}

type Service = 'standard' | 'express'

/** Confirms what is being bought, where it ships and how it will be paid, then opens the
 * checkout (which reserves the stock). Totals come from the server, so they match the charge. */
export function CheckoutStartPage() {
  const { t } = useTranslation()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const { data: me } = useMe()
  const fromCart = params.get('from') === 'cart'
  const listingId = params.get('listing')
  const variantId = params.get('variant')
  const quantity = Number(params.get('qty') ?? '1')
  // Paying an accepted offer: the server charges the agreed price.
  const offerId = params.get('offer')
  const providers = useProviders()
  const [provider, setProvider] = useState<string | null>(null)
  const own = me?.municipality_id && me.city && me.state ? { ibge_code: me.municipality_id, name: me.city, state: me.state } : null
  const [city, setCity] = useState<City | null>(null)
  const destination = city ?? own
  const [service, setService] = useState<Service>('standard')
  // One key per visit: a double tap or a retry reuses the same checkout instead of reserving twice.
  const [idempotencyKey] = useState(() => crypto.randomUUID())

  const what = offerId
    ? { offer_id: offerId }
    : fromCart
      ? { from_cart: true }
      : { items: [{ listing_id: listingId, quantity, ...(variantId ? { variant_id: variantId } : {}) }] }
  const delivery = { ship_to: destination?.ibge_code ?? null, shipping_service: service }

  const quote = useQuery({
    queryKey: ['checkout-quote', what, delivery],
    queryFn: () => api<Quote>('/api/checkouts/quote', { method: 'POST', body: json({ ...what, ...delivery }) }),
    retry: false,
  })
  const needsCity = quote.error instanceof ApiError && quote.error.code === 'destination_required'
  const calculated = Object.keys(quote.data?.shipping_by_service ?? {}).length > 0

  const create = useMutation({
    mutationFn: (chosen: string) =>
      api<Checkout>(offerId ? `/api/offers/${offerId}/checkout` : '/api/checkouts', {
        method: 'POST',
        headers: { 'Idempotency-Key': idempotencyKey },
        body: json(offerId ? { provider: chosen, ...delivery } : { provider: chosen, ...what, ...delivery }),
      }),
    onSuccess: (checkout) => navigate(`/checkout/${checkout.id}`, { replace: true }),
  })

  const chosen = provider ?? providers.data?.[0]?.name ?? null
  const q = quote.data
  const shipping = q?.orders.reduce((s, o) => s + o.shipping_cents, 0) ?? 0

  return (
    <div className="mx-auto max-w-xl space-y-4 pb-20 md:pb-0">
      <PageHeader title={t('checkout.title')} />
      {offerId && q && (
        <p className="flex items-center gap-2 rounded-2xl bg-success-soft px-3 py-2 text-sm text-success">
          <Icon name="check" size={16} />
          {t('chat.offer.agreedPrice', { amount: formatMoney(q.orders[0]?.items[0]?.unit_price_cents ?? 0) })}
        </p>
      )}

      {(calculated || needsCity) && (
        <section className="card space-y-3 p-4">
          <p className="font-semibold">{t('shipping.deliverTo')}</p>
          <CityPicker key={destination?.ibge_code ?? 'none'} value={destination} onChange={setCity} label={t('shipping.to')} />
          {calculated && (
            <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label={t('shipping.title')}>
              {(['standard', 'express'] as const).map((s) => (
                <label key={s} className="flex cursor-pointer items-center justify-between gap-2 rounded-xl border border-line p-3 has-checked:border-primary has-checked:bg-primary-soft">
                  <span className="flex items-center gap-2">
                    <input type="radio" name="service" checked={service === s} onChange={() => setService(s)} />
                    <span className="text-sm font-semibold">{t(`shipping.service.${s}`)}</span>
                  </span>
                  <span className="text-sm">{formatMoney(q?.shipping_by_service[s] ?? 0)}</span>
                </label>
              ))}
            </div>
          )}
        </section>
      )}

      {quote.isPending && <div className="skeleton h-40" />}
      {quote.isError && !needsCity && <p role="alert" className="text-sm text-danger">{errorMessage(quote.error, t)}</p>}
      {q && (
        <section className="card divide-y divide-line">
          {q.orders.flatMap((o) => o.items).map((l, i) => (
            <div key={i} className="flex justify-between gap-4 p-4 text-sm">
              <span>{l.quantity} × {l.title}{l.variant_name ? ` (${l.variant_name})` : ''}</span>
              <span className="font-semibold">{formatMoney(l.unit_price_cents * l.quantity)}</span>
            </div>
          ))}
          <div className="flex justify-between p-4 text-sm">
            <span className="text-muted">
              {t('cart.shipping')}
              {q.orders.some((o) => o.shipping_days) && ` · ${t('shipping.days', { count: Math.max(...q.orders.map((o) => o.shipping_days ?? 0)) })}`}
            </span>
            <span>{formatMoney(shipping)}</span>
          </div>
          <div className="flex justify-between p-4 text-base font-bold"><span>{t('cart.total')}</span><span>{formatMoney(q.total_cents)}</span></div>
        </section>
      )}

      <fieldset className="card space-y-2 p-4">
        <legend className="sr-only">{t('checkout.payWith')}</legend>
        <p className="font-semibold">{t('checkout.payWith')}</p>
        {providers.data?.length === 0 && <p className="text-sm text-danger">{t('checkout.noProviders')}</p>}
        {providers.data?.map((p) => (
          <label key={p.name} className="flex cursor-pointer items-center gap-3 rounded-xl border border-line p-3 has-checked:border-primary has-checked:bg-primary-soft">
            <input type="radio" name="provider" value={p.name} checked={chosen === p.name} onChange={() => setProvider(p.name)} className="accent-[var(--tp-primary)]" />
            <span>
              <span className="block text-sm font-semibold">{t(`checkout.providers.${p.name}.name`, { defaultValue: p.name })}</span>
              <span className="block text-xs text-muted">{t(`checkout.providers.${p.name}.methods`, { defaultValue: '' })}</span>
            </span>
          </label>
        ))}
      </fieldset>

      <p className="flex items-start gap-2 text-xs text-muted"><Icon name="shield" size={16} className="shrink-0" />{t('checkout.reserveNote')}</p>
      {create.error && <p role="alert" className="text-sm text-danger">{errorMessage(create.error, t)}</p>}
      <div className="fixed inset-x-0 bottom-[calc(57px+env(safe-area-inset-bottom))] z-20 border-t border-line bg-surface/95 px-4 py-2.5 backdrop-blur md:static md:border-0 md:bg-transparent md:p-0">
        <button className="btn-primary w-full" disabled={!chosen || !q || create.isPending} onClick={() => chosen && create.mutate(chosen)}>
          {create.isPending ? t('checkout.reserving') : t('checkout.continue', { total: formatMoney(q?.total_cents ?? 0) })}
        </button>
      </div>
    </div>
  )
}
