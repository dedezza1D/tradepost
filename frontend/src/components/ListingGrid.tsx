import { Link } from 'react-router-dom'
import type { ListingCard } from '../api/types'
import { conditionLabel, formatMoney, statusLabel } from '../lib/format'

export function ListingTile({ item, showStatus = false }: { item: ListingCard; showStatus?: boolean }) {
  return (
    <Link
      to={`/listings/${item.id}`}
      className="card group flex flex-col overflow-hidden transition hover:shadow-md"
    >
      <div className="aspect-[4/3] bg-stone-100">
        {item.thumb_url ? (
          <img
            src={item.thumb_url}
            alt=""
            loading="lazy"
            className="h-full w-full object-cover transition group-hover:scale-[1.02]"
          />
        ) : (
          <div className="flex h-full items-center justify-center text-xs text-stone-400">
            No photo
          </div>
        )}
      </div>
      <div className="flex flex-1 flex-col gap-1 p-3">
        <p className="text-lg font-semibold">{formatMoney(item.price_cents)}</p>
        <p className="line-clamp-2 text-sm text-stone-700">{item.title}</p>
        <p className="mt-auto pt-1 text-xs text-stone-500">
          {conditionLabel[item.condition]} · {item.city}/{item.state}
        </p>
        {showStatus && (
          <span className="mt-1 w-fit rounded-full bg-stone-100 px-2 py-0.5 text-xs font-medium text-stone-700">
            {statusLabel[item.status]}
          </span>
        )}
      </div>
    </Link>
  )
}

export function ListingGrid({ items, showStatus }: { items: ListingCard[]; showStatus?: boolean }) {
  return (
    <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      {items.map((item) => (
        <li key={item.id}>
          <ListingTile item={item} showStatus={showStatus} />
        </li>
      ))}
    </ul>
  )
}
