import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { api, json } from '../api/client'
import { useCart } from '../api/orders'
import { formatMoney } from '../lib/format'

export function CartPage() {
  const cart = useCart()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const update = useMutation({
    mutationFn: ({ id, quantity }: { id: string; quantity: number }) =>
      api<void>(`/api/cart/${id}`, { method: 'PUT', body: json({ quantity }) }),
    onSettled: () => qc.invalidateQueries({ queryKey: ['cart'] }),
  })

  if (cart.isPending) return <p className="text-stone-500">Loading…</p>
  if (cart.isError) return <p className="text-red-700">{cart.error.message}</p>
  const { items } = cart.data
  const unavailable = items.filter((i) => i.available < i.quantity)

  return (
    <div className="mx-auto grid max-w-5xl gap-6 lg:grid-cols-[1fr_320px]">
      <section>
        <h1 className="mb-4 text-xl font-semibold">Cart</h1>
        {items.length === 0 ? (
          <div className="card p-10 text-center text-stone-600">
            Your cart is empty.{' '}
            <Link to="/" className="font-medium text-brand-700 hover:underline">
              Browse listings
            </Link>
          </div>
        ) : (
          <ul className="space-y-3">
            {items.map((item) => {
              const short = item.available < item.quantity
              return (
                <li key={item.listing_id} className="card flex gap-4 p-3">
                  <Link to={`/listings/${item.listing_id}`} className="h-20 w-24 shrink-0 overflow-hidden rounded-lg bg-stone-100">
                    {item.thumb_url && <img src={item.thumb_url} alt="" className="h-full w-full object-cover" />}
                  </Link>
                  <div className="flex min-w-0 flex-1 flex-col">
                    <Link to={`/listings/${item.listing_id}`} className="truncate font-medium hover:underline">
                      {item.title}
                    </Link>
                    <span className="text-xs text-stone-500">Sold by {item.seller_name}</span>
                    {short && (
                      <span className="text-xs font-medium text-red-700">
                        {item.available === 0 ? 'No longer available' : `Only ${item.available} left`}
                      </span>
                    )}
                    <div className="mt-auto flex items-center gap-3 pt-2">
                      <label className="sr-only" htmlFor={`qty-${item.listing_id}`}>Quantity</label>
                      <select
                        id={`qty-${item.listing_id}`}
                        className="input w-20 py-1"
                        value={item.quantity}
                        onChange={(e) => update.mutate({ id: item.listing_id, quantity: Number(e.target.value) })}
                      >
                        {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
                          <option key={n}>{n}</option>
                        ))}
                      </select>
                      <button
                        type="button"
                        className="text-sm text-stone-600 hover:text-red-700"
                        onClick={() => update.mutate({ id: item.listing_id, quantity: 0 })}
                      >
                        Remove
                      </button>
                    </div>
                  </div>
                  <p className="font-semibold">{formatMoney(item.price_cents * item.quantity)}</p>
                </li>
              )
            })}
          </ul>
        )}
      </section>
      {items.length > 0 && (
        <aside className="card h-fit space-y-2 p-5 text-sm">
          <div className="flex justify-between"><span>Items</span><span>{formatMoney(cart.data.items_cents)}</span></div>
          <div className="flex justify-between"><span>Shipping</span><span>{formatMoney(cart.data.shipping_cents)}</span></div>
          <div className="flex justify-between border-t border-stone-200 pt-2 text-base font-semibold">
            <span>Total</span><span>{formatMoney(cart.data.total_cents)}</span>
          </div>
          {unavailable.length > 0 && (
            <p className="text-xs text-red-700">Adjust the items marked above before checking out.</p>
          )}
          <button
            className="btn-primary w-full"
            disabled={unavailable.length > 0}
            onClick={() => navigate('/checkout?from=cart')}
          >
            Check out
          </button>
        </aside>
      )}
    </div>
  )
}
