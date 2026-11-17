import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api, json } from '../api/client'
import { type Order, type SalesSummary, useProviders } from '../api/orders'
import { formatMoney } from '../lib/format'
import { OrderItems, StatusBadge } from './OrdersPage'

function Stat({ label, value, hint }: { label: string; value: number; hint: string }) {
  return (
    <div className="card p-4">
      <p className="text-xs font-medium text-stone-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{formatMoney(value)}</p>
      <p className="mt-1 text-xs text-stone-500">{hint}</p>
    </div>
  )
}

export function SellerPage() {
  const summary = useQuery({ queryKey: ['sales-summary'], queryFn: () => api<SalesSummary>('/api/me/sales/summary') })
  const orders = useQuery({ queryKey: ['orders', 'seller'], queryFn: () => api<Order[]>('/api/orders?role=seller') })
  const providers = useProviders()
  const canPayOut = providers.data?.some((p) => p.name === 'stripe')
  const connect = useMutation({
    mutationFn: () => api<{ onboarding_url: string }>('/api/me/payout-account', { method: 'POST', body: json({ provider: 'stripe' }) }),
    onSuccess: (data) => window.location.assign(data.onboarding_url),
  })

  const active = orders.data?.filter((o) => o.status === 'paid' || o.status === 'shipped') ?? []
  const done = orders.data?.filter((o) => ['delivered', 'refunded'].includes(o.status)) ?? []

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">Selling</h1>
        <div className="flex gap-2">
          <Link to="/me/listings" className="btn-secondary">My listings</Link>
          <Link to="/sell" className="btn-primary">New listing</Link>
        </div>
      </div>

      {summary.data && (
        <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label="Balance">
          <Stat label="In escrow" value={summary.data.in_escrow_cents} hint="Paid orders, released when the buyer confirms delivery" />
          <Stat label="To receive" value={summary.data.to_receive_cents} hint="Delivered, payout pending" />
          <Stat label="Paid out" value={summary.data.paid_out_cents} hint="Transferred to your account" />
          <Stat label="Commission" value={summary.data.fees_cents} hint={`Platform fee on ${formatMoney(summary.data.gross_cents)} in sales`} />
        </section>
      )}

      {canPayOut && summary.data && !summary.data.payout_account && (
        <section className="card flex flex-wrap items-center justify-between gap-3 p-5">
          <div>
            <h2 className="font-semibold">Receive payouts</h2>
            <p className="text-sm text-stone-600">
              Connect a Stripe account (test mode) to receive your share of card and Pix sales paid through Stripe.
            </p>
          </div>
          <button className="btn-primary" onClick={() => connect.mutate()} disabled={connect.isPending}>
            {connect.isPending ? 'Opening Stripe…' : 'Connect with Stripe'}
          </button>
          {connect.isError && <p className="w-full text-sm text-red-700">{connect.error.message}</p>}
        </section>
      )}

      <section className="space-y-3">
        <h2 className="font-semibold">To ship ({active.length})</h2>
        {orders.isPending && <p className="text-stone-500">Loading…</p>}
        {active.length === 0 && orders.isSuccess && <p className="text-sm text-stone-500">Nothing to ship right now.</p>}
        {active.map((o) => <SellerOrder key={o.id} order={o} />)}
      </section>

      {done.length > 0 && (
        <section className="space-y-3">
          <h2 className="font-semibold">History</h2>
          {done.map((o) => <SellerOrder key={o.id} order={o} />)}
        </section>
      )}
    </div>
  )
}

function SellerOrder({ order }: { order: Order }) {
  const qc = useQueryClient()
  const [tracking, setTracking] = useState('')
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['orders'] })
    qc.invalidateQueries({ queryKey: ['sales-summary'] })
  }
  const ship = useMutation({
    mutationFn: () => api<Order>(`/api/orders/${order.id}/ship`, { method: 'POST', body: json({ tracking_code: tracking }) }),
    onSuccess: refresh,
  })
  const refund = useMutation({
    mutationFn: () => api<Order>(`/api/orders/${order.id}/refund`, { method: 'POST' }),
    onSuccess: refresh,
  })
  function submit(event: FormEvent) {
    event.preventDefault()
    ship.mutate()
  }
  const error = ship.error ?? refund.error

  return (
    <article className="card space-y-3 p-5">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-xs text-stone-500">{new Date(order.created_at).toLocaleString('pt-BR')}</p>
          <p className="text-sm">Buyer: <strong>{order.buyer.display_name}</strong></p>
        </div>
        <StatusBadge status={order.status} />
      </header>
      <OrderItems order={order} />
      <div className="flex flex-wrap gap-x-6 gap-y-1 border-t border-stone-100 pt-3 text-sm">
        <span>Total {formatMoney(order.total_cents)}</span>
        <span className="text-stone-500">Commission −{formatMoney(order.platform_fee_cents ?? 0)}</span>
        <span className="font-semibold">You receive {formatMoney(order.seller_net_cents ?? 0)}</span>
        {order.tracking_code && <span>Tracking <code>{order.tracking_code}</code></span>}
      </div>
      {order.status === 'paid' && (
        <div className="flex flex-wrap items-end gap-2">
          <form onSubmit={submit} className="flex flex-1 gap-2">
            <label className="sr-only" htmlFor={`tracking-${order.id}`}>Tracking code</label>
            <input id={`tracking-${order.id}`} className="input" required minLength={4} maxLength={60}
              placeholder="Tracking code" value={tracking} onChange={(e) => setTracking(e.target.value)} />
            <button className="btn-primary" disabled={ship.isPending}>Mark shipped</button>
          </form>
          <button
            className="btn-danger"
            disabled={refund.isPending}
            onClick={() => {
              if (confirm('Cancel this order and refund the buyer?')) refund.mutate()
            }}
          >
            Cancel and refund
          </button>
        </div>
      )}
      {error && <p role="alert" className="text-sm text-red-700">{error.message}</p>}
    </article>
  )
}
