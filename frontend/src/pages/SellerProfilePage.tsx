import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { api } from '../api/client'
import { errorMessage } from '../lib/errors'
import { formatDateTime } from '../lib/format'
import { Icon } from '../ui/icons'
import { EmptyState, ErrorState, Stars } from '../ui/primitives'

interface SellerReviews {
  seller_id: string
  display_name: string
  city: string | null
  state: string | null
  member_since: string
  rating_avg: number | null
  rating_count: number
  reviews: { id: string; rating: number; comment: string | null; buyer_name: string; items: string[]; created_at: string }[]
}

export function SellerProfilePage() {
  const { t } = useTranslation()
  const { id } = useParams()
  const data = useQuery({ queryKey: ['seller-reviews', id], queryFn: () => api<SellerReviews>(`/api/users/${id}/reviews`) })
  if (data.isPending) return <div className="mx-auto max-w-2xl space-y-3"><div className="skeleton h-32" /><div className="skeleton h-24" /></div>
  if (data.isError) return <ErrorState message={errorMessage(data.error, t)} onRetry={() => void data.refetch()} />
  const s = data.data
  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <header className="card flex items-center gap-4 p-5">
        <span className="grid h-16 w-16 shrink-0 place-items-center rounded-full bg-primary-soft text-2xl font-bold text-primary">
          {s.display_name.slice(0, 1).toUpperCase()}
        </span>
        <div className="min-w-0 space-y-1">
          <h1 className="truncate text-xl font-bold">{s.display_name}</h1>
          {s.city && <p className="flex items-center gap-1 text-sm text-muted"><Icon name="pin" size={15} />{s.city} - {s.state}</p>}
          <p className="text-sm text-muted">{t('seller.memberSince', { date: new Date(s.member_since).toLocaleDateString() })}</p>
          <p className="flex items-center gap-1.5 text-sm">
            {s.rating_avg !== null ? (
              <><Stars value={s.rating_avg} /> <strong>{s.rating_avg.toFixed(1)}</strong> · {t('reviews.count', { count: s.rating_count })}</>
            ) : <span className="text-muted">{t('reviews.none')}</span>}
          </p>
        </div>
      </header>
      <h2 className="text-lg font-bold">{t('reviews.title')}</h2>
      {s.reviews.length === 0 ? (
        <EmptyState icon="star" title={t('reviews.none')} />
      ) : (
        <ul className="space-y-3">
          {s.reviews.map((r) => (
            <li key={r.id} className="card p-4 text-sm">
              <div className="flex items-center justify-between gap-2">
                <Stars value={r.rating} size={15} />
                <span className="text-xs text-muted">{formatDateTime(r.created_at)}</span>
              </div>
              {r.comment && <p className="mt-2 leading-relaxed">{r.comment}</p>}
              <p className="mt-2 text-xs text-muted">{t('reviews.bought', { name: r.buyer_name, items: r.items.join(', ') })}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
