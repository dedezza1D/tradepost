import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useCategories } from '../api/catalog'
import { ApiError, api, json } from '../api/client'
import { Stars } from './SellerProfilePage'
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
            <BuyBox listingId={item.id} stock={item.stock} available={available} signedIn={Boolean(me)} />
          )}
        </div>
        <Link to={`/users/${item.seller.id}`} className="card block p-5 text-sm hover:shadow-md">
          <p className="text-stone-500">Sold by</p>
          <p className="font-semibold">{item.seller.display_name}</p>
          <p className="mt-1 text-xs text-stone-600">
            {item.seller.rating_avg !== null ? (
              <>
                <Stars value={item.seller.rating_avg} /> {item.seller.rating_avg.toFixed(1)} ·{' '}
                {item.seller.rating_count} review{item.seller.rating_count > 1 ? 's' : ''}
              </>
            ) : (
              'No reviews yet'
            )}
          </p>
        </Link>
      </aside>
    </article>
  )
}

function BuyBox({
  listingId,
  stock,
  available,
  signedIn,
}: {
  listingId: string
  stock: number
  available: boolean
  signedIn: boolean
}) {
  const [quantity, setQuantity] = useState(1)
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [added, setAdded] = useState(false)
  const addToCart = useMutation({
    mutationFn: () => api<void>(`/api/cart/${listingId}`, { method: 'PUT', body: json({ quantity }) }),
    onSuccess: () => {
      setAdded(true)
      qc.invalidateQueries({ queryKey: ['cart'] })
    },
  })
  const requireSignIn = () => navigate('/login', { state: { from: `/listings/${listingId}` } })

  if (!available) return null
  return (
    <div className="space-y-2">
      {stock > 1 && (
        <label className="flex items-center gap-2 text-sm">
          Quantity
          <select className="input w-20 py-1" value={quantity} onChange={(e) => setQuantity(Number(e.target.value))}>
            {Array.from({ length: Math.min(stock, 10) }, (_, i) => i + 1).map((n) => (
              <option key={n}>{n}</option>
            ))}
          </select>
        </label>
      )}
      <button
        type="button"
        className="btn-primary w-full"
        onClick={() => (signedIn ? navigate(`/checkout?listing=${listingId}&qty=${quantity}`) : requireSignIn())}
      >
        Buy now
      </button>
      <button
        type="button"
        className="btn-secondary w-full"
        disabled={addToCart.isPending}
        onClick={() => (signedIn ? addToCart.mutate() : requireSignIn())}
      >
        {added ? 'Added to cart' : 'Add to cart'}
      </button>
      {added && (
        <Link to="/cart" className="block text-center text-sm font-medium text-brand-700 hover:underline">
          Go to cart
        </Link>
      )}
      {addToCart.isError && <p className="text-sm text-red-700">{addToCart.error.message}</p>}
    </div>
  )
}
