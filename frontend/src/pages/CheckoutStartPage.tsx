import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, json } from '../api/client'
import { type Checkout, useCart, useProviders } from '../api/orders'
import type { Listing } from '../api/types'
import { errorMessage } from '../lib/errors'
import { formatMoney } from '../lib/format'
import { Icon } from '../ui/icons'
import { PageHeader } from '../ui/primitives'

/** Confirms what is being bought and how it will be paid, then opens the checkout (which
 * reserves the stock). */
export function CheckoutStartPage() {
  const { t } = useTranslation()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const fromCart = params.get('from') === 'cart'
  const listingId = params.get('listing')
  const quantity = Number(params.get('qty') ?? '1')
  // Paying an accepted offer: the server charges the agreed price; the amount here is only shown.
  const offerId = params.get('offer')
  const agreed = Number(params.get('amount') ?? '0')
  const providers = useProviders()
  const cart = useCart(fromCart)
  const listing = useQuery({ queryKey: ['listing', listingId], queryFn: () => api<Listing>(`/api/listings/${listingId}`), enabled: Boolean(listingId) })
  const [provider, setProvider] = useState<string | null>(null)
  // One key per visit: a double tap or a retry reuses the same checkout instead of reserving twice.
  const [idempotencyKey] = useState(() => crypto.randomUUID())

  const create = useMutation({
    mutationFn: (chosen: string) =>
      offerId
        ? api<Checkout>(`/api/offers/${offerId}/checkout`, {
            method: 'POST',
            headers: { 'Idempotency-Key': idempotencyKey },
            body: json({ provider: chosen }),
          })
        : api<Checkout>('/api/checkouts', {
            method: 'POST',
            headers: { 'Idempotency-Key': idempotencyKey },
            body: json(fromCart ? { provider: chosen, from_cart: true } : { provider: chosen, items: [{ listing_id: listingId, quantity }] }),
          }),
    onSuccess: (checkout) => navigate(`/checkout/${checkout.id}`, { replace: true }),
  })

  const lines = fromCart
    ? (cart.data?.items ?? []).map((i) => ({ title: i.title, quantity: i.quantity, cents: i.price_cents * i.quantity, shipping: i.shipping_cents }))
    : listing.data
      ? [{ title: listing.data.title, quantity, cents: (offerId ? agreed : listing.data.price_cents) * quantity, shipping: listing.data.shipping_cents }]
      : []
  const shipping = lines.reduce((s, l) => s + l.shipping, 0)
  const total = lines.reduce((s, l) => s + l.cents, 0) + shipping
  const chosen = provider ?? providers.data?.[0]?.name ?? null

  return (
    <div className="mx-auto max-w-xl space-y-4 pb-20 md:pb-0">
      <PageHeader title={t('checkout.title')} />
      {offerId && (
        <p className="flex items-center gap-2 rounded-2xl bg-success-soft px-3 py-2 text-sm text-success">
          <Icon name="check" size={16} />
          {t('chat.offer.agreedPrice', { amount: formatMoney(agreed) })}
        </p>
      )}
      <section className="card divide-y divide-line">
        {lines.map((l, i) => (
          <div key={i} className="flex justify-between gap-4 p-4 text-sm">
            <span>{l.quantity} × {l.title}</span>
            <span className="font-semibold">{formatMoney(l.cents)}</span>
          </div>
        ))}
        <div className="flex justify-between p-4 text-sm"><span className="text-muted">{t('cart.shipping')}</span><span>{formatMoney(shipping)}</span></div>
        <div className="flex justify-between p-4 text-base font-bold"><span>{t('cart.total')}</span><span>{formatMoney(total)}</span></div>
      </section>

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
        <button className="btn-primary w-full" disabled={!chosen || lines.length === 0 || create.isPending} onClick={() => chosen && create.mutate(chosen)}>
          {create.isPending ? t('checkout.reserving') : t('checkout.continue', { total: formatMoney(total) })}
        </button>
      </div>
    </div>
  )
}
