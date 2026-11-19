import { Trans, useTranslation } from 'react-i18next'
import { savedSearchLabel, type SavedParams } from '../api/discovery'
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
  'new_follower',
  'listing_saved',
  'saved_price_drop',
  'saved_low_stock',
  'listing_removed',
  'new_message',
  'question_received',
  'dispute_opened',
  'dispute_message',
  'dispute_refunded',
  'dispute_seller_rejected',
  'dispute_rejected',
  'dispute_escalated',
  'dispute_cancelled',
  'ops_alert',
  'question_answered',
  'offer_received',
  'offer_countered',
  'offer_accepted',
  'offer_declined',
  'offer_expired',
  'saved_search',
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
    name: p.name ?? '',
    old: typeof p.old_price_cents === 'number' ? formatMoney(p.old_price_cents) : '',
    price: typeof p.price_cents === 'number' ? formatMoney(p.price_cents) : '',
    offer: typeof p.amount_cents === 'number' ? formatMoney(p.amount_cents) : '',
    alert: typeof p.alert === 'string'
      ? t(`ops.alert.${p.alert.split(':')[0]}`, { name: p.alert.split(':')[1] ?? '', defaultValue: p.alert })
      : '',
  }
  const key = KNOWN.has(n.kind) ? n.kind : 'unknown'
  if (key === 'saved_search') {
    const count = Number(p.count ?? 0)
    const search = savedSearchLabel((n.payload.params ?? {}) as SavedParams, t)
    return <Trans i18nKey="notifications.kind.saved_search" count={count} values={{ count, search }} components={{ b: <strong /> }} />
  }
  return <Trans i18nKey={`notifications.kind.${key}`} values={values} components={{ b: <strong /> }} />
}
