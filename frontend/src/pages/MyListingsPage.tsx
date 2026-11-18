import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { ListingCard } from '../api/types'
import { ListingGrid, ListingGridSkeleton } from '../components/ListingGrid'
import { errorMessage } from '../lib/errors'
import { Icon } from '../ui/icons'
import { EmptyState, ErrorState, PageHeader } from '../ui/primitives'

export function MyListingsPage() {
  const { t } = useTranslation()
  const listings = useQuery({ queryKey: ['my-listings'], queryFn: () => api<ListingCard[]>('/api/me/listings') })
  return (
    <div>
      <PageHeader
        title={t('profile.myListings')}
        back="/me"
        action={<Link to="/sell" className="btn-primary min-h-10 rounded-full"><Icon name="plus" size={18} />{t('nav.sell')}</Link>}
      />
      {listings.isPending && <ListingGridSkeleton count={4} />}
      {listings.isError && <ErrorState message={errorMessage(listings.error, t)} onRetry={() => void listings.refetch()} />}
      {listings.data?.length === 0 && (
        <EmptyState icon="tag" title={t('myListings.emptyTitle')} text={t('myListings.emptyText')} action={{ label: t('nav.sell'), to: '/sell' }} />
      )}
      {listings.data && listings.data.length > 0 && <ListingGrid items={listings.data} showStatus />}
    </div>
  )
}
