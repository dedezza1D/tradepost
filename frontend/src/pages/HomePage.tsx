import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import { useMe } from '../api/auth'
import { useCategories } from '../api/catalog'
import { useListingSearch, useOnVisible } from '../api/listings'
import { ListingGrid, ListingGridSkeleton } from '../components/ListingGrid'
import { errorMessage } from '../lib/errors'
import { Icon } from '../ui/icons'
import { EmptyState, ErrorState, Spinner } from '../ui/primitives'

function Section({ title, link, children }: { title: string; link?: string; children: React.ReactNode }) {
  const { t } = useTranslation()
  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between">
        <h2 className="text-lg font-bold tracking-tight">{title}</h2>
        {link && (
          <Link to={link} className="text-sm font-semibold text-primary hover:underline">
            {t('home.seeAll')}
          </Link>
        )}
      </div>
      {children}
    </section>
  )
}

function NearYou({ city, code }: { city: string; code: number }) {
  const { t } = useTranslation()
  const filters = { near: String(code), radius_km: '50', sort: 'nearest', limit: '8' }
  const results = useListingSearch(filters)
  const items = results.data?.pages[0]?.items ?? []
  if (results.isSuccess && items.length === 0) return null
  return (
    <Section
      title={t('home.nearYou', { city })}
      link={`/search?near=${code}&near_name=${encodeURIComponent(city)}&sort=nearest&radius_km=50`}
    >
      {results.isPending ? <ListingGridSkeleton count={4} /> : <ListingGrid items={items} />}
    </Section>
  )
}

export function HomePage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { data: me } = useMe()
  const { data: categories } = useCategories()
  const latest = useListingSearch({ sort: 'newest' })
  const loadMore = useCallback(() => {
    if (latest.hasNextPage && !latest.isFetchingNextPage) void latest.fetchNextPage()
  }, [latest])
  const sentinel = useOnVisible(loadMore, Boolean(latest.hasNextPage))
  const items = latest.data?.pages.flatMap((p) => p.items) ?? []

  return (
    <div className="space-y-7">
      <button
        type="button"
        onClick={() => navigate('/search')}
        className="input flex items-center gap-2 rounded-full text-left text-muted md:hidden"
      >
        <Icon name="search" size={18} />
        {t('nav.searchPlaceholder')}
      </button>

      <nav aria-label={t('home.categories')} className="-mx-4 overflow-x-auto px-4 no-scrollbar">
        <ul className="flex gap-2">
          {categories?.map((c) => (
            <li key={c.slug} className="shrink-0">
              <Link
                to={`/search?category=${c.slug}`}
                className="inline-flex min-h-10 items-center rounded-full border border-line bg-surface px-4 text-sm font-medium hover:border-primary hover:text-primary"
              >
                {t(`categories.${c.slug}`, { defaultValue: c.name })}
              </Link>
            </li>
          ))}
        </ul>
      </nav>

      {me?.municipality_id && me.city && <NearYou city={me.city} code={me.municipality_id} />}

      <Section title={t('home.latest')}>
        {latest.isPending && <ListingGridSkeleton />}
        {latest.isError && <ErrorState message={errorMessage(latest.error, t)} onRetry={() => void latest.refetch()} />}
        {latest.isSuccess && items.length === 0 && (
          <EmptyState icon="tag" title={t('home.emptyTitle')} text={t('home.emptyText')} action={{ label: t('nav.sell'), to: '/sell' }} />
        )}
        {items.length > 0 && <ListingGrid items={items} />}
        <div ref={sentinel} className="flex justify-center py-4">
          {latest.isFetchingNextPage && <Spinner className="text-primary" />}
        </div>
      </Section>
    </div>
  )
}
