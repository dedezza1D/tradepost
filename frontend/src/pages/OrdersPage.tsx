import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api, json } from '../api/client'
import { type Order, orderStatusLabel } from '../api/orders'
import { formatMoney } from '../lib/format'

export function OrderItems({ order }: { order: Order }) {
  return (
    <ul className="space-y-2">
      {order.items.map((item) => (
        <li key={item.listing_id} className="flex items-center gap-3">
          <div className="h-12 w-14 shrink-0 overflow-hidden rounded-md bg-stone-100">
            {item.thumb_url && <img src={item.thumb_url} alt="" className="h-full w-full object-cover" />}
          </div>
          <Link to={`/listings/${item.listing_id}`} className="flex-1 text-sm hover:underline">
            {item.quantity} × {item.title}
          </Link>
          <span className="text-sm">{formatMoney(item.unit_price_cents * item.quantity)}</span>
        </li>
      ))}
    </ul>
  )
}

export function StatusBadge({ status }: { status: Order['status'] }) {
  const tone: Record<Order['status'], string> = {
    awaiting_payment: 'bg-stone-100 text-stone-700',
    paid: 'bg-blue-50 text-blue-800',
    shipped: 'bg-amber-50 text-amber-900',
    delivered: 'bg-brand-50 text-brand-800',
    cancelled: 'bg-stone-100 text-stone-500',
    refunded: 'bg-red-50 text-red-700',
  }
  return <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${tone[status]}`}>{orderStatusLabel[status]}</span>
}

export function OrdersPage() {
  const orders = useQuery({ queryKey: ['orders', 'buyer'], queryFn: () => api<Order[]>('/api/orders?role=buyer') })
  if (orders.isPending) return <p className="text-stone-500">Loading…</p>
  if (orders.isError) return <p className="text-red-700">{orders.error.message}</p>
  const visible = orders.data.filter((o) => o.status !== 'awaiting_payment' && o.status !== 'cancelled')

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <h1 className="text-xl font-semibold">My orders</h1>
      {visible.length === 0 && <div className="card p-10 text-center text-stone-600">No orders yet.</div>}
      {visible.map((order) => (
        <BuyerOrder key={order.id} order={order} />
      ))}
    </div>
  )
}

function BuyerOrder({ order }: { order: Order }) {
  const qc = useQueryClient()
  const deliver = useMutation({
    mutationFn: () => api<Order>(`/api/orders/${order.id}/deliver`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['orders'] }),
  })
  return (
    <article className="card space-y-3 p-5">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-xs text-stone-500">{new Date(order.created_at).toLocaleString('pt-BR')}</p>
          <p className="text-sm">Sold by <strong>{order.seller.display_name}</strong></p>
        </div>
        <StatusBadge status={order.status} />
      </header>
      <OrderItems order={order} />
      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-stone-100 pt-3 text-sm">
        <span>Total <strong>{formatMoney(order.total_cents)}</strong></span>
        {order.tracking_code && <span className="text-stone-600">Tracking: <code>{order.tracking_code}</code></span>}
        {order.status === 'shipped' && (
          <button className="btn-primary" onClick={() => deliver.mutate()} disabled={deliver.isPending}>
            I received it
          </button>
        )}
      </footer>
      {order.status === 'delivered' && !order.reviewed && <ReviewForm orderId={order.id} />}
      {order.reviewed && <p className="text-xs text-stone-500">Thanks for your review.</p>}
    </article>
  )
}

function ReviewForm({ orderId }: { orderId: string }) {
  const qc = useQueryClient()
  const [rating, setRating] = useState(5)
  const [comment, setComment] = useState('')
  const send = useMutation({
    mutationFn: () => api(`/api/orders/${orderId}/review`, { method: 'POST', body: json({ rating, comment: comment || null }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['orders'] }),
  })
  function submit(event: FormEvent) {
    event.preventDefault()
    send.mutate()
  }
  return (
    <form onSubmit={submit} className="space-y-2 rounded-lg bg-stone-50 p-3">
      <fieldset className="flex items-center gap-1">
        <legend className="mb-1 text-sm font-medium">Rate the seller</legend>
        {[1, 2, 3, 4, 5].map((n) => (
          <button
            key={n}
            type="button"
            aria-label={`${n} star${n > 1 ? 's' : ''}`}
            aria-pressed={n <= rating}
            onClick={() => setRating(n)}
            className={`text-2xl leading-none ${n <= rating ? 'text-amber-500' : 'text-stone-300'}`}
          >
            ★
          </button>
        ))}
      </fieldset>
      <label className="sr-only" htmlFor={`comment-${orderId}`}>Comment</label>
      <textarea id={`comment-${orderId}`} className="input" maxLength={1000} placeholder="How did it go? (optional)"
        value={comment} onChange={(e) => setComment(e.target.value)} />
      {send.isError && <p className="text-sm text-red-700">{send.error.message}</p>}
      <button className="btn-primary" disabled={send.isPending}>Send review</button>
    </form>
  )
}
