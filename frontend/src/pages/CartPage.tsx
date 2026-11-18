import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import { api, json } from '../api/client'
import { useCart } from '../api/orders'
import { errorMessage } from '../lib/errors'
import { formatMoney } from '../lib/format'
import { Icon } from '../ui/icons'
import { EmptyState, ErrorState, PageHeader } from '../ui/primitives'

export function CartPage() {
  const { t } = useTranslation()
  const cart = useCart()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const update = useMutation({
    mutationFn: ({ id, quantity }: { id: string; quantity: number }) =>
      api<void>(`/api/cart/${id}`, { method: 'PUT', body: json({ quantity }) }),
    onSettled: () => qc.invalidateQueries({ queryKey: ['cart'] }),
  })

  if (cart.isPending) return <div className="mx-auto max-w-4xl space-y-3"><div className="skeleton h-28" /><div className="skeleton h-28" /></div>
  if (cart.isError) return <ErrorState message={errorMessage(cart.error, t)} onRetry={() => void cart.refetch()} />
  const { items } = cart.data
  const blocked = items.filter((i) => i.available < i.quantity)

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader title={t('nav.cart')} />
      {items.length === 0 ? (
        <EmptyState icon="cart" title={t('cart.emptyTitle')} text={t('cart.emptyText')} action={{ label: t('cart.browse'), to: '/' }} />
      ) : (
        <div className="grid gap-5 lg:grid-cols-[1fr_320px] lg:items-start">
          <ul className="space-y-3">
            {items.map((item) => {
              const short = item.available < item.quantity
              return (
                <li key={item.listing_id} className="card flex gap-3 p-3">
                  <Link to={`/listings/${item.listing_id}`} className="h-24 w-24 shrink-0 overflow-hidden rounded-xl bg-surface-2">
                    {item.thumb_url && <img src={item.thumb_url} alt="" className="h-full w-full object-cover" />}
                  </Link>
                  <div className="flex min-w-0 flex-1 flex-col">
                    <Link to={`/listings/${item.listing_id}`} className="line-clamp-2 text-sm font-semibold hover:underline">{item.title}</Link>
                    <span className="text-xs text-muted">{t('cart.soldBy', { name: item.seller_name })}</span>
                    {short && (
                      <span className="text-xs font-semibold text-danger">
                        {item.available === 0 ? t('cart.unavailable') : t('cart.onlyLeft', { count: item.available })}
                      </span>
                    )}
                    <div className="mt-auto flex items-center justify-between gap-2 pt-2">
                      <div className="flex items-center gap-2">
                        <label className="sr-only" htmlFor={`qty-${item.listing_id}`}>{t('listing.quantity')}</label>
                        <select id={`qty-${item.listing_id}`} className="input min-h-9 w-20 py-1" value={item.quantity}
                          onChange={(e) => update.mutate({ id: item.listing_id, quantity: Number(e.target.value) })}>
                          {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => <option key={n}>{n}</option>)}
                        </select>
                        <button type="button" className="btn-ghost min-h-9 px-2 text-danger" aria-label={t('cart.remove', { title: item.title })}
                          onClick={() => update.mutate({ id: item.listing_id, quantity: 0 })}>
                          <Icon name="trash" size={18} />
                        </button>
                      </div>
                      <span className="font-bold">{formatMoney(item.price_cents * item.quantity)}</span>
                    </div>
                  </div>
                </li>
              )
            })}
          </ul>
          <aside className="card space-y-2 p-5 text-sm lg:sticky lg:top-24">
            <div className="flex justify-between"><span className="text-muted">{t('cart.items')}</span><span>{formatMoney(cart.data.items_cents)}</span></div>
            <div className="flex justify-between"><span className="text-muted">{t('cart.shipping')}</span><span>{formatMoney(cart.data.shipping_cents)}</span></div>
            <div className="flex justify-between border-t border-line pt-2 text-base font-bold"><span>{t('cart.total')}</span><span>{formatMoney(cart.data.total_cents)}</span></div>
            {blocked.length > 0 && <p className="text-xs text-danger">{t('cart.fixItems')}</p>}
            <button className="btn-primary mt-2 w-full" disabled={blocked.length > 0} onClick={() => navigate('/checkout?from=cart')}>{t('cart.checkout')}</button>
          </aside>
        </div>
      )}
    </div>
  )
}
