import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import {
  NOTIFICATIONS_KEY,
  fetchNotifications,
  markAllRead,
  useNotificationStream,
  type Notification,
} from '../api/notifications'
import { formatDateTime, formatMoney } from '../lib/format'

const KNOWN = new Set([
  'order_paid',
  'purchase_confirmed',
  'order_shipped',
  'order_delivered',
  'order_refunded',
  'payment_refunded',
  'payout_sent',
  'review_received',
])

function target(n: Notification): string {
  const p = n.payload
  if (n.kind === 'order_paid' || n.kind === 'order_delivered' || n.kind === 'payout_sent' || n.kind === 'review_received') {
    return '/seller'
  }
  if (p.order_id) return '/orders'
  return '/'
}

function NotificationText({ n }: { n: Notification }) {
  const { t } = useTranslation()
  const p = n.payload as Record<string, string | number | undefined>
  const more = Number(p.more_items ?? 0)
  const title = `${p.title ?? ''}${more ? t('notifications.moreItems', { count: more }) : ''}`
  const values = {
    title,
    total: typeof p.total_cents === 'number' ? formatMoney(p.total_cents) : '',
    amount: typeof p.amount_cents === 'number' ? formatMoney(p.amount_cents) : '',
    tracking: p.tracking_code ?? '',
    rating: p.rating ?? '',
  }
  const key = KNOWN.has(n.kind) ? n.kind : 'unknown'
  return <Trans i18nKey={`notifications.kind.${key}`} values={values} components={{ b: <strong /> }} />
}

export function NotificationBell() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const panel = useRef<HTMLDivElement>(null)
  useNotificationStream(true)
  const { data } = useQuery({ queryKey: NOTIFICATIONS_KEY, queryFn: fetchNotifications })
  const readAll = useMutation({
    mutationFn: markAllRead,
    onSuccess: () => qc.invalidateQueries({ queryKey: NOTIFICATIONS_KEY }),
  })
  const unread = data?.unread ?? 0

  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (panel.current && !panel.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div className="relative" ref={panel}>
      <button
        type="button"
        className="relative rounded-full p-2 text-stone-700 hover:bg-stone-100"
        aria-label={`${t('notifications.open')}${unread ? ` (${t('notifications.unread', { count: unread })})` : ''}`}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" strokeLinecap="round" />
        </svg>
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 min-w-5 rounded-full bg-red-600 px-1 text-center text-[11px] font-bold leading-5 text-white">
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={t('notifications.title')}
          className="card absolute right-0 z-20 mt-2 w-[min(22rem,calc(100vw-2rem))] overflow-hidden"
        >
          <div className="flex items-center justify-between border-b border-stone-100 px-4 py-3">
            <h2 className="font-semibold">{t('notifications.title')}</h2>
            {unread > 0 && (
              <button className="text-xs font-medium text-brand-700 hover:underline" onClick={() => readAll.mutate()}>
                {t('notifications.markAllRead')}
              </button>
            )}
          </div>
          <ul className="max-h-96 divide-y divide-stone-100 overflow-y-auto">
            {data?.items.length === 0 && (
              <li className="px-4 py-8 text-center text-sm text-stone-500">{t('notifications.empty')}</li>
            )}
            {data?.items.map((n) => (
              <li key={n.id}>
                <Link
                  to={target(n)}
                  onClick={() => setOpen(false)}
                  className={`block px-4 py-3 text-sm hover:bg-stone-50 ${n.read_at ? 'text-stone-600' : 'bg-brand-50/60 text-stone-900'}`}
                >
                  <NotificationText n={n} />
                  <span className="mt-1 block text-xs text-stone-500">{formatDateTime(n.created_at)}</span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
