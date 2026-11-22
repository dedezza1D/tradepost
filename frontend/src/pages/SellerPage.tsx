import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api, json } from '../api/client'
import { type Order, type SalesSummary, useProviders } from '../api/orders'
import { errorMessage } from '../lib/errors'
import { formatDateTime, formatMoney } from '../lib/format'
import { Icon } from '../ui/icons'
import { EmptyState, PageHeader } from '../ui/primitives'
import { OrderItems, StatusBadge } from './OrdersPage'
import { DisputeBadge } from './DisputePages'
import { Coupons } from '../components/Coupons'
import { SellerDashboard } from '../components/SellerDashboard'

function Stat({ label, value, hint }: { label: string; value: number; hint: string }) {
  return (
    <div className="card p-4">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className="mt-1 text-xl font-bold tabular-nums sm:text-2xl">{formatMoney(value)}</p>
      <p className="mt-1 text-xs text-muted">{hint}</p>
    </div>
  )
}

function SellerOrder({ order }: { order: Order }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [tracking, setTracking] = useState('')
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['orders'] })
    void qc.invalidateQueries({ queryKey: ['sales-summary'] })
  }
  const ship = useMutation({
    mutationFn: () => api<Order>(`/api/orders/${order.id}/ship`, { method: 'POST', body: json({ tracking_code: tracking }) }),
    onSuccess: refresh,
  })
  const refund = useMutation({ mutationFn: () => api<Order>(`/api/orders/${order.id}/refund`, { method: 'POST' }), onSuccess: refresh })
  const error = ship.error ?? refund.error

  return (
    <article className="card space-y-3 p-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-xs text-muted">{formatDateTime(order.created_at)}</p>
          <p className="text-sm">{t('seller.buyer')}: <strong>{order.buyer.display_name}</strong></p>
        </div>
        <StatusBadge status={order.status} />
      </header>
      <OrderItems order={order} />
      <dl className="grid grid-cols-3 gap-2 rounded-xl bg-surface-2 p-3 text-xs">
        <div><dt className="text-muted">{t('cart.total')}</dt><dd className="font-semibold">{formatMoney(order.total_cents)}</dd></div>
        <div><dt className="text-muted">{t('seller.commission')}</dt><dd className="font-semibold">−{formatMoney(order.platform_fee_cents ?? 0)}</dd></div>
        <div><dt className="text-muted">{t('seller.youReceive')}</dt><dd className="font-semibold text-success">{formatMoney(order.seller_net_cents ?? 0)}</dd></div>
      </dl>
      {order.tracking_code && <p className="text-sm text-muted">{t('orders.tracking')}: <code className="font-mono">{order.tracking_code}</code></p>}
      {order.dispute_id && order.dispute_status && (
        <Link to={`/disputes/${order.dispute_id}`} className="flex items-center justify-between rounded-xl bg-warning-soft px-3 py-2 text-sm hover:underline">
          {t('disputes.view')} <DisputeBadge status={order.dispute_status} />
        </Link>
      )}
      {order.discount_cents > 0 && <p className="text-sm text-muted">{t('coupons.applied', { amount: formatMoney(order.discount_cents) })}</p>}
      {order.shipping_service && (
        <p className="text-sm text-muted">
          {t(`shipping.service.${order.shipping_service}`)} · {t('shipping.days', { count: order.shipping_days ?? 0 })}
          {order.ship_to ? ` · ${order.ship_to}` : ''}
        </p>
      )}
      {order.status === 'paid' && (
        <div className="space-y-2">
          <form onSubmit={(e: FormEvent) => { e.preventDefault(); ship.mutate() }} className="flex gap-2">
            <label className="sr-only" htmlFor={`tracking-${order.id}`}>{t('seller.trackingCode')}</label>
            <input id={`tracking-${order.id}`} className="input" required minLength={4} maxLength={60} placeholder={t('seller.trackingCode')}
              value={tracking} onChange={(e) => setTracking(e.target.value)} />
            <button className="btn-primary shrink-0" disabled={ship.isPending}><Icon name="truck" size={18} />{t('seller.markShipped')}</button>
          </form>
          <button className="btn-ghost min-h-9 px-2 text-sm text-danger" disabled={refund.isPending}
            onClick={() => { if (confirm(t('seller.refundConfirm'))) refund.mutate() }}>
            {t('seller.cancelRefund')}
          </button>
        </div>
      )}
      {error && <p role="alert" className="text-sm text-danger">{errorMessage(error, t)}</p>}
    </article>
  )
}

export function SellerPage() {
  const { t } = useTranslation()
  const summary = useQuery({ queryKey: ['sales-summary'], queryFn: () => api<SalesSummary>('/api/me/sales/summary') })
  const orders = useQuery({ queryKey: ['orders', 'seller'], queryFn: () => api<Order[]>('/api/orders?role=seller') })
  const providers = useProviders()
  const canPayOut = providers.data?.some((p) => p.name === 'stripe')
  const connect = useMutation({
    mutationFn: () => api<{ onboarding_url: string }>('/api/me/payout-account', { method: 'POST', body: json({ provider: 'stripe' }) }),
    onSuccess: (data) => window.location.assign(data.onboarding_url),
  })
  const toShip = orders.data?.filter((o) => o.status === 'paid' || o.status === 'shipped') ?? []
  const history = orders.data?.filter((o) => o.status === 'delivered' || o.status === 'refunded') ?? []

  return (
    <div className="space-y-6">
      <PageHeader title={t('nav.selling')} action={
        <div className="flex gap-2">
          <Link to="/me/listings" className="btn-secondary hidden min-h-10 rounded-full sm:inline-flex">{t('profile.myListings')}</Link>
          <Link to="/sell" className="btn-primary min-h-10 rounded-full"><Icon name="plus" size={18} />{t('nav.sell')}</Link>
        </div>
      } />
      {summary.data && (
        <section className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label={t('seller.balance')}>
          <Stat label={t('seller.escrow')} value={summary.data.in_escrow_cents} hint={t('seller.escrowHint')} />
          <Stat label={t('seller.toReceive')} value={summary.data.to_receive_cents} hint={t('seller.toReceiveHint')} />
          <Stat label={t('seller.paidOut')} value={summary.data.paid_out_cents} hint={t('seller.paidOutHint')} />
          <Stat label={t('seller.commission')} value={summary.data.fees_cents} hint={t('seller.commissionHint', { gross: formatMoney(summary.data.gross_cents) })} />
        </section>
      )}
      {canPayOut && summary.data && !summary.data.payout_account && (
        <section className="card flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="font-semibold">{t('seller.payoutsTitle')}</h2>
            <p className="text-sm text-muted">{t('seller.payoutsText')}</p>
          </div>
          <button className="btn-primary shrink-0" onClick={() => connect.mutate()} disabled={connect.isPending}>
            {connect.isPending ? t('seller.opening') : t('seller.connectStripe')}
          </button>
          {connect.isError && <p className="text-sm text-danger">{errorMessage(connect.error, t)}</p>}
        </section>
      )}
      <section className="space-y-3">
        <h2 className="font-semibold">{t('seller.toShip', { count: toShip.length })}</h2>
        {orders.isPending && <div className="skeleton h-40" />}
        {orders.isSuccess && toShip.length === 0 && <EmptyState icon="truck" title={t('seller.nothingToShip')} />}
        {toShip.map((o) => <SellerOrder key={o.id} order={o} />)}
      </section>
      <SellerDashboard />
      {history.length > 0 && (
        <section className="space-y-3">
          <h2 className="font-semibold">{t('seller.history')}</h2>
          {history.map((o) => <SellerOrder key={o.id} order={o} />)}
        </section>
      )}
      <Coupons />
    </div>
  )
}
