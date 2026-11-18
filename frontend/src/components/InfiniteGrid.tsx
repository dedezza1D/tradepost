import type { InfiniteData, UseInfiniteQueryResult } from '@tanstack/react-query'
import { useCallback, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useOnVisible } from '../api/listings'
import type { ListingPage } from '../api/types'
import { errorMessage } from '../lib/errors'
import { ErrorState, Spinner } from '../ui/primitives'
import { ListingGrid, ListingGridSkeleton } from './ListingGrid'

/** Listing grid for a paginated query: skeleton, error, empty state and load-more on scroll. */
export function InfiniteGrid({
  query,
  empty,
}: {
  query: UseInfiniteQueryResult<InfiniteData<ListingPage>, Error>
  empty: ReactNode
}) {
  const { t } = useTranslation()
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = query
  const loadMore = useCallback(() => {
    if (hasNextPage && !isFetchingNextPage) void fetchNextPage()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])
  const sentinel = useOnVisible(loadMore, Boolean(hasNextPage))
  const items = query.data?.pages.flatMap((p) => p.items) ?? []

  if (query.isPending) return <ListingGridSkeleton />
  if (query.isError) return <ErrorState message={errorMessage(query.error, t)} onRetry={() => void query.refetch()} />
  if (items.length === 0) return <>{empty}</>
  return (
    <>
      <ListingGrid items={items} />
      <div ref={sentinel} className="flex justify-center py-4">
        {isFetchingNextPage && <Spinner className="text-primary" />}
      </div>
    </>
  )
}
