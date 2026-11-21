import { useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { useMe } from '../api/auth'
import type { ListingCard } from '../api/types'
import { formatMoney } from '../lib/format'
import { Badge } from '../ui/primitives'
import { Icon } from '../ui/icons'
import { SaveButton } from './SaveButton'

export function ListingTile({ item, showStatus = false }: { item: ListingCard; showStatus?: boolean }) {
  const { t } = useTranslation()
  const { data: me } = useMe()
  const own = Boolean(me && item.seller_id === me.id)
  return (
    // The heart sits beside the link, not inside it: a button inside a link is not accessible.
    <div className="group relative h-full">
      <Link
        to={`/listings/${item.id}`}
        className="flex h-full flex-col overflow-hidden rounded-2xl bg-surface shadow-[var(--shadow-card)] ring-1 ring-line/60 transition duration-200 hover:-translate-y-0.5 hover:shadow-[var(--shadow-lift)] focus-visible:ring-2 focus-visible:ring-primary"
      >
        <div className="relative aspect-square overflow-hidden bg-surface-2">
          {item.thumb_url ? (
            <img
              src={item.thumb_url}
              alt=""
              loading="lazy"
              decoding="async"
              className="h-full w-full object-cover transition duration-500 group-hover:scale-[1.04]"
            />
          ) : (
            <div className="grid h-full place-items-center text-muted/60">
              <Icon name="camera" size={32} />
            </div>
          )}
          {item.condition === 'new' && (
            <span className="absolute top-2 left-2 rounded-md bg-primary px-2 py-0.5 text-[11px] font-bold tracking-wide text-primary-fg uppercase shadow-sm">
              {t('listing.condition.new')}
            </span>
          )}
        </div>
        <div className="flex flex-1 flex-col gap-1 border-t border-line/60 p-3">
          <p className="line-clamp-2 min-h-[2.5em] text-[13px] leading-snug text-fg/90 sm:text-sm">{item.title}</p>
          <p className="text-lg font-extrabold tracking-tight sm:text-xl">{formatMoney(item.price_cents)}</p>
          <p className="mt-auto flex items-center gap-1 text-xs text-muted">
            <Icon name="pin" size={13} />
            <span className="truncate">
              {item.city} - {item.state}
              {item.distance_km != null && ` · ${t('near.distance', { value: item.distance_km })}`}
            </span>
          </p>
          {showStatus && (
            <span className="mt-1.5">
              <Badge tone={item.status === 'active' ? 'success' : 'neutral'}>{t(`listing.status.${item.status}`)}</Badge>
            </span>
          )}
        </div>
      </Link>
      {!showStatus && !own && (
        <span className="absolute top-2 right-2">
          <SaveButton id={item.id} saved={Boolean(item.saved)} title={item.title} />
        </span>
      )}
    </div>
  )
}

export function ListingGrid({ items, showStatus }: { items: ListingCard[]; showStatus?: boolean }) {
  return (
    <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 lg:grid-cols-4 xl:grid-cols-5">
      {items.map((item) => (
        <li key={item.id}>
          <ListingTile item={item} showStatus={showStatus} />
        </li>
      ))}
    </ul>
  )
}

/** A horizontal shelf of listings that scrolls by swipe, or with the arrow buttons on desktop. */
export function ListingRail({ items, label }: { items: ListingCard[]; label: string }) {
  const { t } = useTranslation()
  const track = useRef<HTMLUListElement>(null)
  const scroll = (direction: number) =>
    track.current?.scrollBy({ left: direction * track.current.clientWidth * 0.9, behavior: 'smooth' })
  return (
    <div className="group/rail relative">
      <ul
        ref={track}
        aria-label={label}
        className="no-scrollbar -mx-4 flex snap-x snap-mandatory gap-3 overflow-x-auto scroll-px-4 px-4 pt-1 pb-3 sm:gap-4"
      >
        {items.map((item) => (
          <li key={item.id} className="w-40 shrink-0 snap-start sm:w-48 lg:w-52">
            <ListingTile item={item} />
          </li>
        ))}
      </ul>
      {(['previous', 'next'] as const).map((dir) => (
        <button
          key={dir}
          type="button"
          onClick={() => scroll(dir === 'next' ? 1 : -1)}
          aria-label={t(`home.rail.${dir}`)}
          className={`absolute top-[38%] hidden h-11 w-11 -translate-y-1/2 place-items-center rounded-full bg-surface text-fg shadow-[var(--shadow-lift)] ring-1 ring-line transition hover:text-primary md:grid ${
            dir === 'next' ? '-right-4' : '-left-4'
          }`}
        >
          <Icon name={dir === 'next' ? 'chevronRight' : 'chevronLeft'} />
        </button>
      ))}
    </div>
  )
}

export function ListingGridSkeleton({ count = 10 }: { count?: number }) {
  return (
    <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 lg:grid-cols-4 xl:grid-cols-5" aria-hidden>
      {Array.from({ length: count }, (_, i) => (
        <li key={i} className="overflow-hidden rounded-2xl bg-surface shadow-[var(--shadow-card)]">
          <div className="skeleton aspect-square rounded-none" />
          <div className="space-y-2 p-3">
            <div className="skeleton h-4 w-full" />
            <div className="skeleton h-5 w-1/2" />
            <div className="skeleton h-3 w-2/3" />
          </div>
        </li>
      ))}
    </ul>
  )
}
