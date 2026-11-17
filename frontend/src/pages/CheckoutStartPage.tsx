import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ApiError, api, json } from '../api/client'
import { type Checkout, providerLabel, useCart, useProviders } from '../api/orders'
import type { Listing } from '../api/types'
import { formatMoney } from '../lib/format'

/** Confirms what is being bought and with which provider, then opens the checkout (which
 * reserves the stock). */
export function CheckoutStartPage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const fromCart = params.get('from') === 'cart'
  const listingId = params.get('listing')
  const quantity = Number(params.get('qty') ?? '1')
  const providers = useProviders()
  const cart = useCart(fromCart)
  const listing = useQuery({
    queryKey: ['listing', listingId],
    queryFn: () => api<Listing>(`/api/listings/${listingId}`),
    enabled: Boolean(listingId),
  })
  const [provider, setProvider] = useState<string | null>(null)
  // One key per visit to this page: a double click or a retry after a network error reuses
  // the same checkout instead of reserving the stock twice.
  const [idempotencyKey] = useState(() => crypto.randomUUID())

  const create = useMutation({
    mutationFn: (chosen: string) =>
      api<Checkout>('/api/checkouts', {
        method: 'POST',
        headers: { 'Idempotency-Key': idempotencyKey },
        body: json(
          fromCart
            ? { provider: chosen, from_cart: true }
            : { provider: chosen, items: [{ listing_id: listingId, quantity }] },
        ),
      }),
    onSuccess: (checkout) => navigate(`/checkout/${checkout.id}`, { replace: true }),
  })

  const lines = fromCart
    ? (cart.data?.items ?? []).map((i) => ({ title: i.title, quantity: i.quantity, cents: i.price_cents * i.quantity, shipping: i.shipping_cents }))
    : listing.data
      ? [{ title: listing.data.title, quantity, cents: listing.data.price_cents * quantity, shipping: listing.data.shipping_cents }]
      : []
  const total = lines.reduce((sum, l) => sum + l.cents + l.shipping, 0)
  const chosen = provider ?? providers.data?.[0]?.name ?? null
  const error = create.error instanceof ApiError ? create.error : null

  return (
    <div className="mx-auto max-w-xl space-y-4">
      <h1 className="text-xl font-semibold">Checkout</h1>
      <section className="card divide-y divide-stone-100">
        {lines.map((l, i) => (
          <div key={i} className="flex justify-between gap-4 p-4 text-sm">
            <span>{l.quantity} × {l.title}</span>
            <span className="font-medium">{formatMoney(l.cents)}</span>
          </div>
        ))}
        <div className="flex justify-between p-4 text-sm">
          <span>Shipping</span>
          <span>{formatMoney(lines.reduce((s, l) => s + l.shipping, 0))}</span>
        </div>
        <div className="flex justify-between p-4 text-base font-semibold">
          <span>Total</span>
          <span>{formatMoney(total)}</span>
        </div>
      </section>

      <fieldset className="card space-y-2 p-4">
        <legend className="px-1 text-sm font-semibold">Pay with</legend>
        {providers.data?.length === 0 && <p className="text-sm text-red-700">No payment provider is configured.</p>}
        {providers.data?.map((p) => (
          <label key={p.name} className="flex cursor-pointer items-center gap-3 rounded-lg border border-stone-200 p-3 has-checked:border-brand-600 has-checked:bg-brand-50">
            <input type="radio" name="provider" value={p.name} checked={chosen === p.name} onChange={() => setProvider(p.name)} />
            <span className="text-sm font-medium">{providerLabel[p.name] ?? p.name}</span>
          </label>
        ))}
      </fieldset>

      <p className="text-xs text-stone-500">
        The items are reserved for you for 15 minutes once you continue. Payments run in test mode.
      </p>
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error.code === 'out_of_stock' ? 'One of the items is no longer available in that quantity.' : error.message}
        </p>
      )}
      <button
        className="btn-primary w-full"
        disabled={!chosen || lines.length === 0 || create.isPending}
        onClick={() => chosen && create.mutate(chosen)}
      >
        {create.isPending ? 'Reserving…' : `Continue to payment · ${formatMoney(total)}`}
      </button>
    </div>
  )
}
