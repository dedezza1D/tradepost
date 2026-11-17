import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useCategories } from '../api/catalog'
import { ApiError, api } from '../api/client'
import type { Listing } from '../api/types'
import { useMe } from '../api/auth'
import { conditionLabel, formatMoney, statusLabel } from '../lib/format'

export function ListingPage() {
  const { id } = useParams()
  const { data: me } = useMe()
  const { data: categories } = useCategories()
  const [photoIndex, setPhotoIndex] = useState(0)
  const listing = useQuery({
    queryKey: ['listing', id],
    queryFn: () => api<Listing>(`/api/listings/${id}`),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
  })

  if (listing.isPending) return <p className="text-stone-500">Loading…</p>
  if (listing.isError) {
    return (
      <div className="card p-10 text-center">
        <p className="text-stone-700">
          {listing.error instanceof ApiError && listing.error.status === 404
            ? 'This listing does not exist or is no longer available.'
            : listing.error.message}
        </p>
        <Link to="/" className="btn-secondary mt-4">
          Back to listings
        </Link>
      </div>
    )
  }

  const item = listing.data
  const photo = item.photos[Math.min(photoIndex, item.photos.length - 1)]
  const isOwner = me?.id === item.seller.id
  const available = item.status === 'active' && item.stock > 0
  const category = categories?.find((c) => c.slug === item.category_slug)

  return (
    <article className="grid gap-6 lg:grid-cols-[1fr_360px] lg:items-start">
      <div className="space-y-3">
        <div className="card flex aspect-[4/3] items-center justify-center overflow-hidden bg-stone-100">
          {photo ? (
            <img src={photo.url} alt={item.title} className="h-full w-full object-contain" />
          ) : (
            <span className="text-stone-400">No photo</span>
          )}
        </div>
        {item.photos.length > 1 && (
          <ul className="flex gap-2 overflow-x-auto" aria-label="Photos">
            {item.photos.map((p, i) => (
              <li key={p.id}>
                <button
                  type="button"
                  onClick={() => setPhotoIndex(i)}
                  aria-label={`Show photo ${i + 1}`}
                  aria-current={i === photoIndex}
                  className={`h-16 w-20 overflow-hidden rounded-lg border-2 ${i === photoIndex ? 'border-brand-600' : 'border-transparent'}`}
                >
                  <img src={p.thumb_url} alt="" className="h-full w-full object-cover" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <section className="card order-last p-5 lg:col-start-1 lg:row-start-2">
        <h2 className="mb-2 font-semibold">Description</h2>
        <p className="whitespace-pre-line text-sm leading-relaxed text-stone-700">
          {item.description}
        </p>
      </section>

      {/* Phones: photos, then this box, then the description. Desktop: right column. */}
      <aside className="space-y-4 lg:col-start-2 lg:row-span-2 lg:row-start-1">
        <div className="card space-y-3 p-5">
          <p className="text-xs text-stone-500">
            {conditionLabel[item.condition]} · {category?.name ?? item.category_slug}
          </p>
          <h1 className="text-xl font-semibold leading-snug">{item.title}</h1>
          <p className="text-3xl font-bold">{formatMoney(item.price_cents)}</p>
          <p className="text-sm text-stone-600">
            {item.shipping_cents > 0
              ? `Shipping ${formatMoney(item.shipping_cents)}`
              : 'Free shipping or pickup'}
          </p>
          <p className="text-sm text-stone-600">
            {item.city}/{item.state} · {item.stock} available
          </p>
          {item.status !== 'active' && (
            <p className="rounded-lg bg-stone-100 px-3 py-2 text-sm text-stone-700">
              {statusLabel[item.status]}
            </p>
          )}
          {isOwner ? (
            <Link to={`/sell/${item.id}`} className="btn-primary w-full">
              Edit listing
            </Link>
          ) : (
            <button
              type="button"
              className="btn-primary w-full"
              disabled={!available}
              title="Checkout arrives with the payments milestone"
            >
              Buy now
            </button>
          )}
        </div>
        <div className="card p-5 text-sm">
          <p className="text-stone-500">Sold by</p>
          <p className="font-semibold">{item.seller.display_name}</p>
        </div>
      </aside>
    </article>
  )
}
