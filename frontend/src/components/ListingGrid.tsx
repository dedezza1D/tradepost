import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import type { ListingCard } from '../api/types'
import { formatMoney } from '../lib/format'
import { Badge } from '../ui/primitives'
import { Icon } from '../ui/icons'

export function ListingTile({ item, showStatus = false }: { item: ListingCard; showStatus?: boolean }) {
  const { t } = useTranslation()
  return (
    <Link
      to={`/listings/${item.id}`}
      className="group flex flex-col overflow-hidden rounded-2xl bg-surface ring-1 ring-line transition hover:shadow-md focus-visible:ring-2 focus-visible:ring-primary"
    >
      <div className="relative aspect-square bg-surface-2">
        {item.thumb_url ? (
          <img
            src={item.thumb_url}
            alt=""
            loading="lazy"
            decoding="async"
            className="h-full w-full object-cover transition duration-300 group-hover:scale-[1.03]"
          />
        ) : (
          <div className="grid h-full place-items-center text-muted">
            <Icon name="camera" />
          </div>
        )}
        {item.condition === 'new' && (
          <span className="absolute top-2 left-2">
            <Badge tone="primary">{t('listing.condition.new')}</Badge>
          </span>
        )}
      </div>
      <div className="flex flex-1 flex-col gap-0.5 p-2.5 sm:p-3">
        <p className="text-base font-bold tracking-tight sm:text-lg">{formatMoney(item.price_cents)}</p>
        <p className="line-clamp-2 text-sm leading-snug">{item.title}</p>
        <p className="mt-auto flex items-center gap-1 pt-1 text-xs text-muted">
          <Icon name="pin" size={13} />
          <span className="truncate">
            {item.city}
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
  )
}

export function ListingGrid({ items, showStatus }: { items: ListingCard[]; showStatus?: boolean }) {
  return (
    <ul className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 sm:gap-3 lg:grid-cols-4">
      {items.map((item) => (
        <li key={item.id}>
          <ListingTile item={item} showStatus={showStatus} />
        </li>
      ))}
    </ul>
  )
}

export function ListingGridSkeleton({ count = 8 }: { count?: number }) {
  return (
    <ul className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 sm:gap-3 lg:grid-cols-4" aria-hidden>
      {Array.from({ length: count }, (_, i) => (
        <li key={i} className="overflow-hidden rounded-2xl bg-surface ring-1 ring-line">
          <div className="skeleton aspect-square rounded-none" />
          <div className="space-y-2 p-3">
            <div className="skeleton h-5 w-1/2" />
            <div className="skeleton h-4 w-full" />
            <div className="skeleton h-3 w-2/3" />
          </div>
        </li>
      ))}
    </ul>
  )
}
