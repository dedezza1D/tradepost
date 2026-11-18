import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMe } from '../api/auth'
import { useCategories } from '../api/catalog'
import { ApiError, api, json } from '../api/client'
import type { Listing, Photo, Variant } from '../api/types'
import { errorMessage } from '../lib/errors'
import { formatMoney } from '../lib/format'
import { Icon } from '../ui/icons'
import { Badge, EmptyState, ErrorState, Sheet, Stars } from '../ui/primitives'
import { Avatar } from '../components/Avatar'
import { ReportSheet } from '../components/ReportSheet'
import { SaveButton } from '../components/SaveButton'
import { useStartConversation } from '../api/chat'
import { Questions } from '../components/Questions'
import { ShippingQuote } from '../components/ShippingQuote'
import { ReputationBadge } from '../components/Reputation'

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

const QUICK = ['available', 'condition', 'pickup', 'shipping'] as const

function VariantPicker({ variants, value, onChange }: { variants: Variant[]; value: string | null; onChange: (id: string) => void }) {
  const { t } = useTranslation()
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-semibold">{t('listing.chooseOption')}</legend>
      <div className="flex flex-wrap gap-2">
        {variants.map((v) => (
          <label key={v.id} className={`relative ${v.stock === 0 ? 'opacity-50' : ''}`}>
            <input type="radio" name="variant" className="peer sr-only" checked={value === v.id} disabled={v.stock === 0} onChange={() => onChange(v.id)} />
            <span className="inline-flex min-h-10 cursor-pointer items-center rounded-xl border border-line px-3.5 text-sm font-medium peer-checked:border-primary peer-checked:bg-primary-soft peer-checked:text-primary peer-focus-visible:ring-2 peer-focus-visible:ring-primary peer-disabled:cursor-not-allowed peer-disabled:line-through">
              {v.name}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}

function ContactSeller({ listing, signedIn, variantId }: { listing: Listing; signedIn: boolean; variantId: string | null }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const start = useStartConversation()
  function open(body?: string, offer = false) {
    if (!signedIn) {
      navigate('/login', { state: { from: `/listings/${listing.id}` } })
      return
    }
    start.mutate(
      { listing_id: listing.id, body },
      { onSuccess: (c) => navigate(`/inbox/${c.id}${offer ? `?offer=1${variantId ? `&variant=${variantId}` : ''}` : ''}`) },
    )
  }
  return (
    <section className="card space-y-3 p-4" aria-labelledby="contact-title">
      <h2 id="contact-title" className="font-semibold">{t('chat.contactTitle')}</h2>
      <div className="flex flex-wrap gap-2">
        {QUICK.map((q) => (
          <button key={q} type="button" disabled={start.isPending} onClick={() => open(t(`chat.quick.${q}`))}
            className="rounded-full border border-line px-3 py-1.5 text-sm hover:border-primary hover:text-primary">
            {t(`chat.quick.${q}`)}
          </button>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <button type="button" className="btn-secondary" disabled={start.isPending} onClick={() => open()}>
          <Icon name="chat" size={18} />{t('chat.message')}
        </button>
        {listing.status === 'active' && (
          <button type="button" className="btn-secondary" disabled={start.isPending} onClick={() => open(undefined, true)}>
            <Icon name="tag" size={18} />{t('chat.offer.make')}
          </button>
        )}
      </div>
      {start.isError && <p role="alert" className="text-sm text-danger">{errorMessage(start.error, t)}</p>}
    </section>
  )
}

function BuyBox({ listing, signedIn, variant }: { listing: Listing; signedIn: boolean; variant: Variant | null }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [quantity, setQuantity] = useState(1)
  const [added, setAdded] = useState(false)
  const [pickFirst, setPickFirst] = useState(false)
  const needsVariant = listing.variants.length > 0
  const stock = variant ? variant.stock : listing.stock
  const addToCart = useMutation({
    mutationFn: () => api<void>(`/api/cart/${listing.id}`, { method: 'PUT', body: json({ quantity, variant_id: variant?.id }) }),
    onSuccess: () => {
      setAdded(true)
      void qc.invalidateQueries({ queryKey: ['cart'] })
    },
  })
  const needSignIn = () => navigate('/login', { state: { from: `/listings/${listing.id}` } })
  const ready = () => {
    if (needsVariant && !variant) {
      setPickFirst(true)
      return false
    }
    setPickFirst(false)
    return true
  }
  const buy = () => {
    if (!signedIn) return needSignIn()
    if (ready()) navigate(`/checkout?listing=${listing.id}&qty=${quantity}${variant ? `&variant=${variant.id}` : ''}`)
  }
  const add = () => (signedIn ? ready() && addToCart.mutate() : needSignIn())

  return (
    <div className="space-y-3">
      {stock > 1 && (
        <label className="flex items-center gap-3 text-sm">
          {t('listing.quantity')}
          <select className="input w-24" value={quantity} onChange={(e) => setQuantity(Number(e.target.value))}>
            {Array.from({ length: Math.min(stock, 10) }, (_, i) => i + 1).map((n) => <option key={n}>{n}</option>)}
          </select>
          <span className="text-muted">{t('listing.available', { count: stock })}</span>
        </label>
      )}
      <div className="hidden gap-2 md:grid">
        <button type="button" className="btn-primary" onClick={buy}>{t('listing.buyNow')}</button>
        <button type="button" className="btn-secondary" disabled={addToCart.isPending} onClick={add}>
          <Icon name="cart" size={18} />
          {added ? t('listing.addedToCart') : t('listing.addToCart')}
        </button>
      </div>
      {pickFirst && <p role="alert" className="text-sm text-danger">{t('errors.variant_required')}</p>}
      {added && <Link to="/cart" className="block text-sm font-semibold text-primary hover:underline">{t('listing.goToCart')}</Link>}
      {addToCart.isError && <p className="text-sm text-danger">{errorMessage(addToCart.error, t)}</p>}

      {/* Phones: the main actions stay reachable above the bottom navigation. */}
      <div className="fixed inset-x-0 bottom-[calc(57px+env(safe-area-inset-bottom))] z-20 flex gap-2 border-t border-line bg-surface/95 px-4 py-2.5 backdrop-blur md:hidden">
        <button type="button" className="btn-secondary px-3" aria-label={t('listing.addToCart')} disabled={addToCart.isPending} onClick={add}>
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
  const [reporting, setReporting] = useState(false)
  const [variantId, setVariantId] = useState<string | null>(null)
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
  const variant = item.variants.find((v) => v.id === variantId) ?? null

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
              {item.shipping_mode === 'calculated'
                ? t('shipping.calculated')
                : item.shipping_cents > 0 ? t('listing.shipping', { price: formatMoney(item.shipping_cents) }) : t('listing.freeShipping')}</li>
            <li className="flex items-center gap-2"><Icon name="pin" size={18} />{item.city} - {item.state}</li>
            <li className="flex items-center gap-2"><Icon name="shield" size={18} />{t('listing.protected')}</li>
          </ul>
          {item.status !== 'active' && <Badge tone="warning">{t(`listing.status.${item.status}`)}</Badge>}
        </div>

        {isOwner ? (
          <Link to={`/sell/${item.id}`} className="btn-primary w-full">{t('listing.edit')}</Link>
        ) : available ? (
          <>
            {item.variants.length > 0 && <VariantPicker variants={item.variants} value={variantId} onChange={setVariantId} />}
            <BuyBox listing={item} signedIn={Boolean(me)} variant={variant} />
          </>
        ) : null}
        {item.shipping_mode === 'calculated' && <ShippingQuote listingId={item.id} />}
        {!isOwner && <ContactSeller listing={item} signedIn={Boolean(me)} variantId={variantId} />}

        <Link to={`/users/${item.seller.id}`} className="card flex items-center gap-3 p-4 hover:bg-surface-2">
          <Avatar name={item.seller.display_name} url={item.seller.avatar_url} />
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-2">
              <span className="truncate font-semibold">{item.seller.display_name}</span>
              <ReputationBadge level={item.seller.reputation} />
            </span>
            <span className="flex items-center gap-1 text-xs text-muted">
              {item.seller.rating_avg !== null ? (
                <><Stars value={item.seller.rating_avg} size={13} /> {item.seller.rating_avg.toFixed(1)} · {t('reviews.count', { count: item.seller.rating_count })}</>
              ) : t('reviews.none')}
            </span>
          </span>
          <Icon name="chevronRight" className="text-muted" />
        </Link>
        <div className="flex flex-wrap items-center gap-1">
          {!isOwner && <SaveButton id={item.id} saved={item.saved} title={item.title} variant="inline" />}
          <ShareButton title={item.title} />
          {item.save_count > 0 && (
            <span className="ml-auto text-xs text-muted">{t('social.savedBy', { count: item.save_count })}</span>
          )}
        </div>
        {me && !isOwner && (
          <button type="button" className="flex items-center gap-1.5 text-xs text-muted hover:text-danger" onClick={() => setReporting(true)}>
            <Icon name="flag" size={14} />
            {t('social.report.listing')}
          </button>
        )}
        <ReportSheet open={reporting} onClose={() => setReporting(false)} target={{ type: 'listing', id: item.id }} />
      </aside>

      <section className="card p-5 md:col-start-1">
        <h2 className="mb-2 font-semibold">{t('listing.description')}</h2>
        <p className="text-[15px] leading-relaxed whitespace-pre-line">{item.description}</p>
      </section>
      <div className="md:col-start-1">
        <Questions listingId={item.id} isSeller={isOwner} signedIn={Boolean(me)} />
      </div>
    </article>
  )
}
