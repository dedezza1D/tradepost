import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMe } from '../api/auth'
import { useCategories } from '../api/catalog'
import { ApiError, api, json } from '../api/client'
import type { Listing, Photo } from '../api/types'
import { errorMessage } from '../lib/errors'
import { formatMoney } from '../lib/format'
import { Icon } from '../ui/icons'
import { Badge, EmptyState, ErrorState, Sheet, Stars } from '../ui/primitives'

function Gallery({ photos, title }: { photos: Photo[]; title: string }) {
  const { t } = useTranslation()
  const track = useRef<HTMLDivElement>(null)
  const [index, setIndex] = useState(0)
  const [zoom, setZoom] = useState<Photo | null>(null)

  function go(i: number) {
    const el = track.current
    if (el) el.scrollTo({ left: i * el.clientWidth, behavior: 'smooth' })
  }

  if (photos.length === 0) {
    return (
      <div className="grid aspect-square place-items-center rounded-3xl bg-surface-2 text-muted md:aspect-[4/3]">
        <Icon name="camera" size={40} />
      </div>
    )
  }
  return (
    <div className="space-y-3">
      <div className="relative -mx-4 overflow-hidden md:mx-0 md:rounded-3xl">
        <div
          ref={track}
          className="flex snap-x snap-mandatory overflow-x-auto bg-surface-2 no-scrollbar"
          onScroll={(e) => setIndex(Math.round(e.currentTarget.scrollLeft / e.currentTarget.clientWidth))}
          aria-roledescription="carousel"
          aria-label={t('listing.photos')}
        >
          {photos.map((p, i) => (
            <button
              key={p.id}
              type="button"
              className="aspect-square w-full shrink-0 snap-center md:aspect-[4/3]"
              onClick={() => setZoom(p)}
              aria-label={t('listing.openPhoto', { n: i + 1, total: photos.length })}
            >
              <img src={p.url} alt={i === 0 ? title : ''} className="h-full w-full object-contain" decoding="async" />
            </button>
          ))}
        </div>
        {photos.length > 1 && (
          <>
            <span className="absolute right-3 bottom-3 rounded-full bg-black/60 px-2.5 py-1 text-xs font-semibold text-white">
              {index + 1}/{photos.length}
            </span>
            <button type="button" onClick={() => go(Math.max(index - 1, 0))} aria-label={t('listing.previous')}
              className="absolute top-1/2 left-2 hidden -translate-y-1/2 rounded-full bg-surface/90 p-2 shadow md:block">
              <Icon name="chevronLeft" />
            </button>
            <button type="button" onClick={() => go(Math.min(index + 1, photos.length - 1))} aria-label={t('listing.next')}
              className="absolute top-1/2 right-2 hidden -translate-y-1/2 rounded-full bg-surface/90 p-2 shadow md:block">
              <Icon name="chevronRight" />
            </button>
          </>
        )}
      </div>
      {photos.length > 1 && (
        <ul className="hidden gap-2 md:flex">
          {photos.map((p, i) => (
            <li key={p.id}>
              <button type="button" onClick={() => go(i)} aria-label={t('listing.showPhoto', { n: i + 1 })} aria-current={i === index}
                className={`h-16 w-16 overflow-hidden rounded-xl ring-2 ${i === index ? 'ring-primary' : 'ring-transparent'}`}>
                <img src={p.thumb_url} alt="" className="h-full w-full object-cover" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <Sheet open={zoom !== null} onClose={() => setZoom(null)} title={title}>
        {zoom && <img src={zoom.url} alt="" className="mx-auto max-h-[70dvh] w-auto touch-pinch-zoom object-contain" />}
      </Sheet>
    </div>
  )
}

function ShareButton({ title }: { title: string }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  async function share() {
    const url = window.location.href
    if (navigator.share) {
      try {
        await navigator.share({ title, url })
      } catch {
        // Cancelled by the user.
      }
      return
    }
    await navigator.clipboard.writeText(url)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }
  return (
    <button type="button" className="btn-ghost min-h-10 rounded-full px-3" onClick={() => void share()}>
      <Icon name="share" size={18} />
      {copied ? t('listing.linkCopied') : t('listing.share')}
    </button>
  )
}

function BuyBox({ listing, signedIn }: { listing: Listing; signedIn: boolean }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [quantity, setQuantity] = useState(1)
  const [added, setAdded] = useState(false)
  const addToCart = useMutation({
    mutationFn: () => api<void>(`/api/cart/${listing.id}`, { method: 'PUT', body: json({ quantity }) }),
    onSuccess: () => {
      setAdded(true)
      void qc.invalidateQueries({ queryKey: ['cart'] })
    },
  })
  const needSignIn = () => navigate('/login', { state: { from: `/listings/${listing.id}` } })
  const buy = () => (signedIn ? navigate(`/checkout?listing=${listing.id}&qty=${quantity}`) : needSignIn())

  return (
    <div className="space-y-3">
      {listing.stock > 1 && (
        <label className="flex items-center gap-3 text-sm">
          {t('listing.quantity')}
          <select className="input w-24" value={quantity} onChange={(e) => setQuantity(Number(e.target.value))}>
            {Array.from({ length: Math.min(listing.stock, 10) }, (_, i) => i + 1).map((n) => <option key={n}>{n}</option>)}
          </select>
          <span className="text-muted">{t('listing.available', { count: listing.stock })}</span>
        </label>
      )}
      <div className="hidden gap-2 md:grid">
        <button type="button" className="btn-primary" onClick={buy}>{t('listing.buyNow')}</button>
        <button type="button" className="btn-secondary" disabled={addToCart.isPending}
          onClick={() => (signedIn ? addToCart.mutate() : needSignIn())}>
          <Icon name="cart" size={18} />
          {added ? t('listing.addedToCart') : t('listing.addToCart')}
        </button>
      </div>
      {added && <Link to="/cart" className="block text-sm font-semibold text-primary hover:underline">{t('listing.goToCart')}</Link>}
      {addToCart.isError && <p className="text-sm text-danger">{errorMessage(addToCart.error, t)}</p>}

      {/* Phones: the main actions stay reachable above the bottom navigation. */}
      <div className="fixed inset-x-0 bottom-[calc(57px+env(safe-area-inset-bottom))] z-20 flex gap-2 border-t border-line bg-surface/95 px-4 py-2.5 backdrop-blur md:hidden">
        <button type="button" className="btn-secondary px-3" aria-label={t('listing.addToCart')} disabled={addToCart.isPending}
          onClick={() => (signedIn ? addToCart.mutate() : needSignIn())}>
          <Icon name={added ? 'check' : 'cart'} />
        </button>
        <button type="button" className="btn-primary flex-1" onClick={buy}>
          {t('listing.buyNowFor', { price: formatMoney(listing.price_cents * quantity) })}
        </button>
      </div>
    </div>
  )
}

export function ListingPage() {
  const { t } = useTranslation()
  const { id } = useParams()
  const { data: me } = useMe()
  const { data: categories } = useCategories()
  const listing = useQuery({
    queryKey: ['listing', id],
    queryFn: () => api<Listing>(`/api/listings/${id}`),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
  })

  if (listing.isPending) {
    return (
      <div className="grid gap-6 md:grid-cols-[1fr_380px]" aria-busy>
        <div className="skeleton aspect-square md:aspect-[4/3]" />
        <div className="space-y-3"><div className="skeleton h-8 w-1/2" /><div className="skeleton h-6 w-full" /><div className="skeleton h-40 w-full" /></div>
      </div>
    )
  }
  if (listing.isError) {
    return listing.error instanceof ApiError && listing.error.status === 404 ? (
      <EmptyState icon="tag" title={t('listing.notFound')} action={{ label: t('listing.backToListings'), to: '/' }} />
    ) : (
      <ErrorState message={errorMessage(listing.error, t)} onRetry={() => void listing.refetch()} />
    )
  }

  const item = listing.data
  const isOwner = me?.id === item.seller.id
  const available = item.status === 'active' && item.stock > 0
  const category = categories?.find((c) => c.slug === item.category_slug)

  return (
    <article className="grid gap-6 pb-16 md:grid-cols-[1fr_380px] md:items-start md:pb-0">
      <Gallery photos={item.photos} title={item.title} />

      <aside className="space-y-4 md:sticky md:top-24">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            <Badge tone={item.condition === 'new' ? 'primary' : 'neutral'}>{t(`listing.condition.${item.condition}`)}</Badge>
            <Link to={`/search?category=${item.category_slug}`} className="hover:underline">
              {t(`categories.${item.category_slug}`, { defaultValue: category?.name ?? item.category_slug })}
            </Link>
          </div>
          <h1 className="text-xl leading-snug font-bold tracking-tight sm:text-2xl">{item.title}</h1>
          <p className="text-3xl font-extrabold tracking-tight">{formatMoney(item.price_cents)}</p>
          <ul className="space-y-1.5 text-sm text-muted">
            <li className="flex items-center gap-2"><Icon name="truck" size={18} />
              {item.shipping_cents > 0 ? t('listing.shipping', { price: formatMoney(item.shipping_cents) }) : t('listing.freeShipping')}</li>
            <li className="flex items-center gap-2"><Icon name="pin" size={18} />{item.city} - {item.state}</li>
            <li className="flex items-center gap-2"><Icon name="shield" size={18} />{t('listing.protected')}</li>
          </ul>
          {item.status !== 'active' && <Badge tone="warning">{t(`listing.status.${item.status}`)}</Badge>}
        </div>

        {isOwner ? (
          <Link to={`/sell/${item.id}`} className="btn-primary w-full">{t('listing.edit')}</Link>
        ) : available ? (
          <BuyBox listing={item} signedIn={Boolean(me)} />
        ) : null}

        <Link to={`/users/${item.seller.id}`} className="card flex items-center gap-3 p-4 hover:bg-surface-2">
          <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-primary-soft font-bold text-primary">
            {item.seller.display_name.slice(0, 1).toUpperCase()}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate font-semibold">{item.seller.display_name}</span>
            <span className="flex items-center gap-1 text-xs text-muted">
              {item.seller.rating_avg !== null ? (
                <><Stars value={item.seller.rating_avg} size={13} /> {item.seller.rating_avg.toFixed(1)} · {t('reviews.count', { count: item.seller.rating_count })}</>
              ) : t('reviews.none')}
            </span>
          </span>
          <Icon name="chevronRight" className="text-muted" />
        </Link>
        <ShareButton title={item.title} />
      </aside>

      <section className="card p-5 md:col-start-1">
        <h2 className="mb-2 font-semibold">{t('listing.description')}</h2>
        <p className="text-[15px] leading-relaxed whitespace-pre-line">{item.description}</p>
      </section>
    </article>
  )
}
