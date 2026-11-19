import { useCallback, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { useMe } from '../api/auth'
import { useCategories } from '../api/catalog'
import { sameSearch, savedParamsOf, useDeleteSavedSearch, useSaveSearch, useSavedSearches } from '../api/discovery'
import { useListingSearch, useOnVisible } from '../api/listings'
import { BR_STATES } from '../api/types'
import { CityPicker } from '../components/CityPicker'
import { ListingGrid, ListingGridSkeleton } from '../components/ListingGrid'
import { SearchBox } from '../components/SearchBox'
import { errorMessage } from '../lib/errors'
import { formatMoney, parseMoney } from '../lib/format'
import { Icon } from '../ui/icons'
import { EmptyState, ErrorState, Sheet, Spinner } from '../ui/primitives'

const API_KEYS = ['q', 'category', 'condition', 'state', 'min_price', 'max_price', 'sort', 'near', 'radius_km'] as const
const RADII = [5, 10, 25, 50, 100, 250, 500]

type Params = URLSearchParams
type Update = (changes: Record<string, string | null>) => void

function priceParam(raw: string): string | null {
  const cents = parseMoney(raw)
  return cents === null ? null : String(cents)
}

function Filters({ params, update }: { params: Params; update: Update }) {
  const { t } = useTranslation()
  const { data: categories } = useCategories()
  const near = params.get('near')
  return (
    <div className="space-y-5">
      <div>
        <label className="label" htmlFor="f-category">{t('search.category')}</label>
        <select id="f-category" className="input" value={params.get('category') ?? ''} onChange={(e) => update({ category: e.target.value })}>
          <option value="">{t('search.allCategories')}</option>
          {categories?.map((c) => (
            <option key={c.slug} value={c.slug}>{t(`categories.${c.slug}`, { defaultValue: c.name })}</option>
          ))}
        </select>
      </div>
      <fieldset>
        <legend className="label">{t('search.condition')}</legend>
        <div className="flex gap-2">
          {(['', 'new', 'used'] as const).map((value) => (
            <label key={value || 'any'} className="flex-1">
              <input type="radio" name="condition" className="peer sr-only" checked={(params.get('condition') ?? '') === value} onChange={() => update({ condition: value || null })} />
              <span className="flex min-h-10 cursor-pointer items-center justify-center rounded-xl border border-line text-sm font-medium peer-checked:border-primary peer-checked:bg-primary-soft peer-checked:text-primary peer-focus-visible:ring-2 peer-focus-visible:ring-primary">
                {value ? t(`listing.condition.${value}`) : t('search.any')}
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset>
        <legend className="label">{t('search.price')}</legend>
        <div className="flex items-center gap-2">
          <input aria-label={t('search.minPrice')} className="input" inputMode="decimal" placeholder={t('search.min')}
            defaultValue={params.get('min_price') ? String(Number(params.get('min_price')) / 100) : ''}
            onBlur={(e) => update({ min_price: priceParam(e.target.value) })} />
          <span className="text-muted">–</span>
          <input aria-label={t('search.maxPrice')} className="input" inputMode="decimal" placeholder={t('search.max')}
            defaultValue={params.get('max_price') ? String(Number(params.get('max_price')) / 100) : ''}
            onBlur={(e) => update({ max_price: priceParam(e.target.value) })} />
        </div>
      </fieldset>
      <div className="space-y-2">
        <CityPicker
          label={t('near.title')}
          allowCep={false}
          value={near ? { ibge_code: Number(near), name: params.get('near_name') ?? '', state: params.get('near_uf') ?? '' } : null}
          onChange={(city) => update({ near: city ? String(city.ibge_code) : null, near_name: city?.name ?? null, near_uf: city?.state ?? null })}
        />
        {near && (
          <label className="flex items-center gap-2 text-sm text-muted">
            {t('near.radius')}
            <select className="input w-auto" value={params.get('radius_km') ?? '50'} onChange={(e) => update({ radius_km: e.target.value })}>
              {RADII.map((r) => <option key={r} value={r}>{t('near.km', { value: r })}</option>)}
            </select>
          </label>
        )}
      </div>
      {!near && (
        <div>
          <label className="label" htmlFor="f-state">{t('search.state')}</label>
          <select id="f-state" className="input" value={params.get('state') ?? ''} onChange={(e) => update({ state: e.target.value })}>
            <option value="">{t('search.anywhere')}</option>
            {BR_STATES.map((s) => <option key={s}>{s}</option>)}
          </select>
        </div>
      )}
    </div>
  )
}

function ActiveChips({ params, update }: { params: Params; update: Update }) {
  const { t } = useTranslation()
  const chips: { key: string; label: string; clear: Record<string, null> }[] = []
  const category = params.get('category')
  if (category) chips.push({ key: 'category', label: t(`categories.${category}`, { defaultValue: category }), clear: { category: null } })
  const condition = params.get('condition')
  if (condition) chips.push({ key: 'condition', label: t(`listing.condition.${condition}`), clear: { condition: null } })
  const min = params.get('min_price')
  const max = params.get('max_price')
  if (min || max) {
    const label = min && max ? `${formatMoney(Number(min))} – ${formatMoney(Number(max))}` : min ? `≥ ${formatMoney(Number(min))}` : `≤ ${formatMoney(Number(max))}`
    chips.push({ key: 'price', label, clear: { min_price: null, max_price: null } })
  }
  if (params.get('near')) {
    chips.push({
      key: 'near',
      label: `${params.get('near_name') ?? ''} · ${t('near.km', { value: params.get('radius_km') ?? 50 })}`,
      clear: { near: null, near_name: null, near_uf: null, radius_km: null },
    })
  }
  const state = params.get('state')
  if (state) chips.push({ key: 'state', label: state, clear: { state: null } })
  if (!chips.length) return null
  return (
    <ul className="flex flex-wrap gap-2">
      {chips.map((c) => (
        <li key={c.key}>
          <button className="inline-flex min-h-8 items-center gap-1 rounded-full bg-primary-soft px-3 text-xs font-semibold text-primary"
            onClick={() => update(c.clear)} aria-label={t('search.removeFilter', { name: c.label })}>
            {c.label}
            <Icon name="close" size={14} />
          </button>
        </li>
      ))}
    </ul>
  )
}

/** Follow the current search: new matching listings arrive as notifications. */
function SaveSearchButton({ params }: { params: Params }) {
  const { t } = useTranslation()
  const { data: me } = useMe()
  const current = savedParamsOf(params)
  const saved = useSavedSearches(Boolean(me && current))
  const save = useSaveSearch()
  const remove = useDeleteSavedSearch()
  if (!me || !current) return null
  const match = saved.data?.find((s) => sameSearch(s.params, current))
  const busy = save.isPending || remove.isPending || saved.isPending
  return (
    <button
      type="button"
      className={`min-h-10 rounded-full ${match ? 'btn-primary' : 'btn-secondary'}`}
      aria-pressed={Boolean(match)}
      disabled={busy}
      onClick={() => (match ? remove.mutate(match.id) : save.mutate(current))}
      title={match ? t('search.savedHint') : t('search.saveHint')}
    >
      <Icon name="bell" size={18} />
      {match ? t('search.saved') : t('search.save')}
    </button>
  )
}

export function SearchPage() {
  const { t } = useTranslation()
  const [params, setParams] = useSearchParams()
  const [sheet, setSheet] = useState(false)
  const [q, setQ] = useState(params.get('q') ?? '')
  const filters = Object.fromEntries(API_KEYS.flatMap((k) => (params.get(k) ? [[k, params.get(k) as string]] : [])))
  const results = useListingSearch(filters)
  const loadMore = useCallback(() => {
    if (results.hasNextPage && !results.isFetchingNextPage) void results.fetchNextPage()
  }, [results])
  const sentinel = useOnVisible(loadMore, Boolean(results.hasNextPage))
  const items = results.data?.pages.flatMap((p) => p.items) ?? []
  const approximate = results.data?.pages[0]?.approximate ?? false
  const filterCount = ['category', 'condition', 'min_price', 'max_price', 'near', 'state'].filter((k) => params.get(k)).length

  const update: Update = (changes) => {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v)
      else next.delete(k)
    }
    if (!next.get('near') && next.get('sort') === 'nearest') next.delete('sort')
    setParams(next, { replace: true })
  }

  return (
    <div className="grid gap-6 md:grid-cols-[260px_1fr]">
      <aside className="hidden md:block" aria-label={t('search.filters')}>
        <div className="card sticky top-20 p-4">
          <Filters params={params} update={update} />
        </div>
      </aside>

      <section className="min-w-0 space-y-4" aria-live="polite">
        <SearchBox id="search-q" className="md:hidden" value={q} onChange={setQ} onSearch={(query) => update({ q: query || null })} />

        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 className="text-lg font-bold tracking-tight sm:text-xl">
            {params.get('q') ? t('search.resultsFor', { q: params.get('q') }) : t('search.title')}
          </h1>
          <div className="flex flex-wrap items-center gap-2">
            <SaveSearchButton params={params} />
            <button className="btn-secondary min-h-10 rounded-full md:hidden" onClick={() => setSheet(true)}>
              <Icon name="filter" size={18} />
              {t('search.filters')}
              {filterCount > 0 && <span className="rounded-full bg-primary px-1.5 text-xs text-primary-fg">{filterCount}</span>}
            </button>
            <label className="sr-only" htmlFor="sort">{t('search.sortBy')}</label>
            <select id="sort" className="input min-h-10 w-auto rounded-full py-2" value={params.get('sort') ?? ''} onChange={(e) => update({ sort: e.target.value })}>
              <option value="">{params.get('q') ? t('search.sort.relevance') : t('search.sort.newest')}</option>
              <option value="newest">{t('search.sort.newest')}</option>
              <option value="price_asc">{t('search.sort.priceAsc')}</option>
              <option value="price_desc">{t('search.sort.priceDesc')}</option>
              {params.get('near') && <option value="nearest">{t('near.nearest')}</option>}
            </select>
          </div>
        </div>

        <ActiveChips params={params} update={update} />

        {results.isPending && <ListingGridSkeleton />}
        {results.isError && <ErrorState message={errorMessage(results.error, t)} onRetry={() => void results.refetch()} />}
        {results.isSuccess && items.length === 0 && <EmptyState icon="search" title={t('search.emptyTitle')} text={t('search.emptyText')} />}
        {items.length > 0 && approximate && (
          <p role="status" className="mb-3 flex items-start gap-2 text-sm text-muted">
            <Icon name="search" size={16} className="mt-0.5 shrink-0" />
            {t('search.similarOnly')}
          </p>
        )}
        {items.length > 0 && <ListingGrid items={items} />}
        <div ref={sentinel} className="flex justify-center py-4">
          {results.isFetchingNextPage && <Spinner className="text-primary" />}
        </div>
      </section>

      <Sheet open={sheet} onClose={() => setSheet(false)} title={t('search.filters')}
        footer={<button className="btn-primary w-full" onClick={() => setSheet(false)}>{t('search.showResults')}</button>}>
        <Filters params={params} update={update} />
      </Sheet>
    </div>
  )
}
