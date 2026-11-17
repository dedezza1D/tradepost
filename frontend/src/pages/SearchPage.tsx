import { useInfiniteQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { useCategories } from '../api/catalog'
import { api } from '../api/client'
import { BR_STATES, type ListingPage } from '../api/types'
import { ListingGrid } from '../components/ListingGrid'
import { parseMoney } from '../lib/format'

const FILTER_KEYS = ['q', 'category', 'condition', 'state', 'min_price', 'max_price', 'sort'] as const

export function SearchPage() {
  const [params, setParams] = useSearchParams()
  const { data: categories } = useCategories()
  const filters = Object.fromEntries(
    FILTER_KEYS.flatMap((k) => (params.get(k) ? [[k, params.get(k) as string]] : [])),
  )

  const results = useInfiniteQuery({
    queryKey: ['listings', filters],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => {
      const query = new URLSearchParams(filters)
      if (pageParam) query.set('cursor', pageParam)
      return api<ListingPage>(`/api/listings?${query}`)
    },
    getNextPageParam: (last) => last.next_cursor,
  })

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  function setPrice(key: 'min_price' | 'max_price', raw: string) {
    const cents = parseMoney(raw)
    setFilter(key, cents === null ? '' : String(cents))
  }

  const items = results.data?.pages.flatMap((p) => p.items) ?? []
  const q = params.get('q')

  return (
    <div className="grid gap-6 md:grid-cols-[220px_1fr]">
      <aside className="space-y-4" aria-label="Filters">
        <div>
          <label className="label" htmlFor="f-category">
            Category
          </label>
          <select
            id="f-category"
            className="input"
            value={params.get('category') ?? ''}
            onChange={(e) => setFilter('category', e.target.value)}
          >
            <option value="">All categories</option>
            {categories?.map((c) => (
              <option key={c.slug} value={c.slug}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="f-condition">
            Condition
          </label>
          <select
            id="f-condition"
            className="input"
            value={params.get('condition') ?? ''}
            onChange={(e) => setFilter('condition', e.target.value)}
          >
            <option value="">Any</option>
            <option value="new">New</option>
            <option value="used">Used</option>
          </select>
        </div>
        <div>
          <span className="label">Price (R$)</span>
          <div className="flex gap-2">
            <input
              aria-label="Minimum price"
              className="input"
              inputMode="decimal"
              placeholder="Min"
              defaultValue={params.get('min_price') ? Number(params.get('min_price')) / 100 : ''}
              onBlur={(e) => setPrice('min_price', e.target.value)}
            />
            <input
              aria-label="Maximum price"
              className="input"
              inputMode="decimal"
              placeholder="Max"
              defaultValue={params.get('max_price') ? Number(params.get('max_price')) / 100 : ''}
              onBlur={(e) => setPrice('max_price', e.target.value)}
            />
          </div>
        </div>
        <div>
          <label className="label" htmlFor="f-state">
            State
          </label>
          <select
            id="f-state"
            className="input"
            value={params.get('state') ?? ''}
            onChange={(e) => setFilter('state', e.target.value)}
          >
            <option value="">Anywhere</option>
            {BR_STATES.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </div>
      </aside>

      <section aria-live="polite">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          <h1 className="text-xl font-semibold">
            {q ? <>Results for “{q}”</> : 'Latest listings'}
          </h1>
          <label className="flex items-center gap-2 text-sm text-stone-600">
            Sort by
            <select
              className="input w-auto"
              value={params.get('sort') ?? 'relevance'}
              onChange={(e) => setFilter('sort', e.target.value === 'relevance' ? '' : e.target.value)}
            >
              <option value="relevance">{q ? 'Best match' : 'Newest'}</option>
              <option value="newest">Newest</option>
              <option value="price_asc">Lowest price</option>
              <option value="price_desc">Highest price</option>
            </select>
          </label>
        </div>

        {results.isPending && <p className="text-stone-500">Loading…</p>}
        {results.isError && <p className="text-red-700">{results.error.message}</p>}
        {results.isSuccess && items.length === 0 && (
          <div className="card p-10 text-center text-stone-600">
            No listings match these filters.
          </div>
        )}
        <ListingGrid items={items} />
        {results.hasNextPage && (
          <div className="mt-6 text-center">
            <button
              className="btn-secondary"
              disabled={results.isFetchingNextPage}
              onClick={() => results.fetchNextPage()}
            >
              {results.isFetchingNextPage ? 'Loading…' : 'Load more'}
            </button>
          </div>
        )}
      </section>
    </div>
  )
}
