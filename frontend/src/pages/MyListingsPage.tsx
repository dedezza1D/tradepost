import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { ListingCard } from '../api/types'
import { ListingGrid } from '../components/ListingGrid'

export function MyListingsPage() {
  const listings = useQuery({
    queryKey: ['my-listings'],
    queryFn: () => api<ListingCard[]>('/api/me/listings'),
  })

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold">My listings</h1>
        <Link to="/sell" className="btn-primary">
          New listing
        </Link>
      </div>
      {listings.isPending && <p className="text-stone-500">Loading…</p>}
      {listings.isError && <p className="text-red-700">{listings.error.message}</p>}
      {listings.data?.length === 0 && (
        <div className="card p-10 text-center text-stone-600">
          You have no listings yet.
        </div>
      )}
      {listings.data && <ListingGrid items={listings.data} showStatus />}
    </div>
  )
}
