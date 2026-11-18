import { Trans, useTranslation } from 'react-i18next'
import type { Notification } from '../api/notifications'
import { formatMoney } from '../lib/format'

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
export function NotificationText({ n }: { n: Notification }) {
  const { t } = useTranslation()
  const p = n.payload as Record<string, string | number | undefined>
  const more = Number(p.more_items ?? 0)
  const values = {
    title: `${p.title ?? ''}${more ? t('notifications.moreItems', { count: more }) : ''}`,
    total: typeof p.total_cents === 'number' ? formatMoney(p.total_cents) : '',
    amount: typeof p.amount_cents === 'number' ? formatMoney(p.amount_cents) : '',
    tracking: p.tracking_code ?? '',
    rating: p.rating ?? '',
  }
  const key = KNOWN.has(n.kind) ? n.kind : 'unknown'
  return <Trans i18nKey={`notifications.kind.${key}`} values={values} components={{ b: <strong /> }} />
}
