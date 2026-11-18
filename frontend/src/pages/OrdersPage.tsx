import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api, json } from '../api/client'
import type { Order } from '../api/orders'
import { errorMessage } from '../lib/errors'
import { formatDateTime, formatMoney } from '../lib/format'
import { Icon } from '../ui/icons'
import { Badge, EmptyState, ErrorState, PageHeader } from '../ui/primitives'

const STATUS_TONE = {
  awaiting_payment: 'neutral',
  paid: 'primary',
  shipped: 'warning',
  delivered: 'success',
  cancelled: 'neutral',
  refunded: 'danger',
} as const

export function StatusBadge({ status }: { status: Order['status'] }) {
  const { t } = useTranslation()
  return <Badge tone={STATUS_TONE[status]}>{t(`orders.status.${status}`)}</Badge>
}

const STEPS = ['paid', 'shipped', 'delivered'] as const

/** paid → shipped → delivered progress, so buyers see where the order is at a glance. */
export function OrderProgress({ status }: { status: Order['status'] }) {
  const { t } = useTranslation()
  if (!STEPS.includes(status as (typeof STEPS)[number])) return null
  const reached = STEPS.indexOf(status as (typeof STEPS)[number])
  return (
    <ol className="grid grid-cols-3 gap-1.5" aria-label={t('orders.progress')}>
      {STEPS.map((s, i) => (
        <li key={s}>
          <div className={`h-1.5 rounded-full ${i <= reached ? 'bg-primary' : 'bg-surface-2'}`} />
          <span className={`mt-1 block text-[11px] font-medium ${i <= reached ? 'text-fg' : 'text-muted'}`}>{t(`orders.status.${s}`)}</span>
        </li>
      ))}
    </ol>
  )
}

export function OrderItems({ order }: { order: Order }) {
  return (
    <ul className="space-y-2">
      {order.items.map((item, i) => (
        <li key={`${item.listing_id}-${i}`} className="flex items-center gap-3">
          <div className="h-14 w-14 shrink-0 overflow-hidden rounded-xl bg-surface-2">
            {item.thumb_url && <img src={item.thumb_url} alt="" className="h-full w-full object-cover" />}
          </div>
          <Link to={`/listings/${item.listing_id}`} className="line-clamp-2 flex-1 text-sm hover:underline">{item.quantity} × {item.title}{item.variant_name ? ` (${item.variant_name})` : ''}</Link>
          <span className="text-sm font-semibold">{formatMoney(item.unit_price_cents * item.quantity)}</span>
        </li>
      ))}
    </ul>
  )
}

function ReviewForm({ orderId }: { orderId: string }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [rating, setRating] = useState(5)
  const [comment, setComment] = useState('')
  const send = useMutation({
    mutationFn: () => api(`/api/orders/${orderId}/review`, { method: 'POST', body: json({ rating, comment: comment || null }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['orders'] }),
  })
  return (
    <form onSubmit={(e: FormEvent) => { e.preventDefault(); send.mutate() }} className="space-y-3 rounded-2xl bg-surface-2 p-4">
      <fieldset>
        <legend className="label">{t('reviews.rateSeller')}</legend>
        <div className="flex gap-1">
          {[1, 2, 3, 4, 5].map((n) => (
            <button key={n} type="button" aria-label={t('reviews.starsLabel', { value: n })} aria-pressed={n <= rating} onClick={() => setRating(n)}
              className={n <= rating ? 'text-accent' : 'text-muted opacity-40'}>
              <Icon name="star" size={30} fill={n <= rating ? 'currentColor' : 'none'} />
            </button>
          ))}
        </div>
      </fieldset>
      <label className="sr-only" htmlFor={`comment-${orderId}`}>{t('reviews.comment')}</label>
      <textarea id={`comment-${orderId}`} className="input" maxLength={1000} placeholder={t('reviews.commentPlaceholder')} value={comment} onChange={(e) => setComment(e.target.value)} />
      {send.isError && <p className="text-sm text-danger">{errorMessage(send.error, t)}</p>}
      <button className="btn-primary" disabled={send.isPending}>{t('reviews.send')}</button>
    </form>
  )
}

function BuyerOrder({ order }: { order: Order }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const deliver = useMutation({
    mutationFn: () => api<Order>(`/api/orders/${order.id}/deliver`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['orders'] }),
  })
  return (
    <article className="card space-y-4 p-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-xs text-muted">{formatDateTime(order.created_at)}</p>
          <p className="text-sm">{t('orders.soldBy')} <Link to={`/users/${order.seller.id}`} className="font-semibold hover:underline">{order.seller.display_name}</Link></p>
        </div>
        <StatusBadge status={order.status} />
      </header>
      <OrderProgress status={order.status} />
      <OrderItems order={order} />
      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-3 text-sm">
        <span>{t('cart.total')} <strong>{formatMoney(order.total_cents)}</strong></span>
        {order.tracking_code && <span className="text-muted">{t('orders.tracking')}: <code className="font-mono">{order.tracking_code}</code></span>}
        {order.shipping_service && (
          <span className="text-muted">
            {t(`shipping.service.${order.shipping_service}`)} · {t('shipping.days', { count: order.shipping_days ?? 0 })}
            {order.ship_to ? ` · ${order.ship_to}` : ''}
          </span>
        )}
        {order.status === 'shipped' && (
          <button className="btn-primary" onClick={() => deliver.mutate()} disabled={deliver.isPending}>{t('orders.received')}</button>
        )}
      </footer>
      {order.status === 'delivered' && !order.reviewed && <ReviewForm orderId={order.id} />}
      {order.reviewed && <p className="text-xs text-muted">{t('reviews.thanks')}</p>}
    </article>
  )
}

export function OrdersPage() {
  const { t } = useTranslation()
  const orders = useQuery({ queryKey: ['orders', 'buyer'], queryFn: () => api<Order[]>('/api/orders?role=buyer') })
  const visible = orders.data?.filter((o) => o.status !== 'awaiting_payment' && o.status !== 'cancelled') ?? []
  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader title={t('orders.title')} />
      {orders.isPending && <div className="space-y-3"><div className="skeleton h-40" /><div className="skeleton h-40" /></div>}
      {orders.isError && <ErrorState message={errorMessage(orders.error, t)} onRetry={() => void orders.refetch()} />}
      {orders.isSuccess && visible.length === 0 && <EmptyState icon="box" title={t('orders.emptyTitle')} action={{ label: t('cart.browse'), to: '/' }} />}
      <div className="space-y-3">{visible.map((o) => <BuyerOrder key={o.id} order={o} />)}</div>
    </div>
  )
}
