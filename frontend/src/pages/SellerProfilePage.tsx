import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { api } from '../api/client'

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

export function Stars({ value }: { value: number }) {
  return (
    <span aria-label={`${value.toFixed(1)} out of 5`} className="text-amber-500">
      {'★'.repeat(Math.round(value))}
      <span className="text-stone-300">{'★'.repeat(5 - Math.round(value))}</span>
    </span>
  )
}

export function SellerProfilePage() {
  const { id } = useParams()
  const data = useQuery({ queryKey: ['seller-reviews', id], queryFn: () => api<SellerReviews>(`/api/users/${id}/reviews`) })
  if (data.isPending) return <p className="text-stone-500">Loading…</p>
  if (data.isError) return <p className="text-red-700">{data.error.message}</p>
  const s = data.data
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <header className="card p-6">
        <h1 className="text-xl font-semibold">{s.display_name}</h1>
        <p className="text-sm text-stone-600">
          {[s.city, s.state].filter(Boolean).join('/')} · member since {new Date(s.member_since).toLocaleDateString('pt-BR')}
        </p>
        <p className="mt-2 text-sm">
          {s.rating_avg !== null ? (
            <>
              <Stars value={s.rating_avg} /> {s.rating_avg.toFixed(1)} from {s.rating_count} review{s.rating_count > 1 ? 's' : ''}
            </>
          ) : (
            'No reviews yet'
          )}
        </p>
      </header>
      <ul className="space-y-3">
        {s.reviews.map((r) => (
          <li key={r.id} className="card p-4 text-sm">
            <div className="flex justify-between">
              <Stars value={r.rating} />
              <span className="text-xs text-stone-500">{new Date(r.created_at).toLocaleDateString('pt-BR')}</span>
            </div>
            {r.comment && <p className="mt-2 text-stone-800">{r.comment}</p>}
            <p className="mt-2 text-xs text-stone-500">{r.buyer_name} bought {r.items.join(', ')}</p>
          </li>
        ))}
      </ul>
    </div>
  )
}
