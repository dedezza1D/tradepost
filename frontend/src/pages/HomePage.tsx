import { useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router-dom'
import { useMe } from '../api/auth'
import { useCategories } from '../api/catalog'
import { useListingSearch } from '../api/listings'
import { useFeed } from '../api/social'
import type { FeedTab, Me } from '../api/types'
import { CityPicker, type City } from '../components/CityPicker'
import { HeroCarousel } from '../components/HeroCarousel'
import { InfiniteGrid } from '../components/InfiniteGrid'
import { ListingRail } from '../components/ListingGrid'
import { CategoryIcon } from '../ui/categoryIcons'
import { Icon, type IconName } from '../ui/icons'
import { EmptyState } from '../ui/primitives'

const TABS: { id: FeedTab; icon: IconName }[] = [
  { id: 'trending', icon: 'trending' },
  { id: 'nearby', icon: 'pin' },
  { id: 'following', icon: 'users' },
]
const NEAR_KEY = 'tp_near'

function readNear(): City | null {
  try {
    const raw = localStorage.getItem(NEAR_KEY)
    return raw ? (JSON.parse(raw) as City) : null
  } catch {
    return null
  }
}

function Trending() {
  const { t } = useTranslation()
  const feed = useFeed('trending')
  return (
    <InfiniteGrid
      query={feed}
      empty={<EmptyState icon="tag" title={t('home.emptyTitle')} text={t('home.emptyText')} action={{ label: t('nav.sell'), to: '/sell' }} />}
    />
  )
}

function Nearby({ me }: { me: Me | null | undefined }) {
  const { t } = useTranslation()
  const own = me?.municipality_id && me.city && me.state ? { ibge_code: me.municipality_id, name: me.city, state: me.state } : null
  const [picked, setPicked] = useState<City | null>(readNear)
  const city = picked ?? own
  const feed = useFeed('nearby', city ? { near: String(city.ibge_code), radius_km: '50' } : {}, Boolean(city))

  function choose(next: City | null) {
    setPicked(next)
    try {
      if (next) localStorage.setItem(NEAR_KEY, JSON.stringify(next))
      else localStorage.removeItem(NEAR_KEY)
    } catch {
      // Private mode: the choice lasts for this visit only.
    }
  }

  return (
    <div className="space-y-4">
      <div className="card p-4">
        <CityPicker key={city?.ibge_code ?? 'none'} value={city} onChange={choose} label={t('feed.nearCity')} />
        {city && <p className="mt-2 text-xs text-muted">{t('feed.nearHint', { city: city.name })}</p>}
      </div>
      {city ? (
        <InfiniteGrid query={feed} empty={<EmptyState icon="pin" title={t('feed.nearEmpty', { city: city.name })} />} />
      ) : (
        <EmptyState icon="pin" title={t('feed.nearPick')} />
      )}
    </div>
  )
}

function Following({ me }: { me: Me | null | undefined }) {
  const { t } = useTranslation()
  const feed = useFeed('following', {}, Boolean(me))
  if (!me) {
    return <EmptyState icon="users" title={t('feed.followingSignIn')} text={t('feed.followingText')} action={{ label: t('nav.signIn'), to: '/login' }} />
  }
  return (
    <InfiniteGrid
      query={feed}
      empty={<EmptyState icon="users" title={t('feed.followingEmpty')} text={t('feed.followingText')} action={{ label: t('feed.tabs.trending'), to: '/' }} />}
    />
  )
}

function SectionTitle({ id, title, to }: { id: string; title: string; to?: string }) {
  const { t } = useTranslation()
  return (
    <div className="mb-3 flex items-end justify-between gap-3">
      <h2 id={id} className="text-lg font-extrabold tracking-tight sm:text-xl">{title}</h2>
      {to && (
        <Link to={to} className="flex items-center gap-0.5 text-sm font-semibold text-primary hover:underline">
          {t('home.seeAll')}
          <Icon name="chevronRight" size={16} />
        </Link>
      )}
    </div>
  )
}

/** Shortcut cards under the banner, in the manner of a shop window. */
function Shortcuts() {
  const { t } = useTranslation()
  const { data: me } = useMe()
  const cards: { id: string; icon: IconName; to: string }[] = [
    { id: 'nearby', icon: 'pin', to: '/?tab=nearby' },
    { id: 'saved', icon: 'heart', to: me ? '/me/saved' : '/login' },
    { id: 'following', icon: 'users', to: '/?tab=following' },
    { id: 'sell', icon: 'tag', to: me ? '/sell' : '/login' },
  ]
  return (
    <ul className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
      {cards.map((c) => (
        <li key={c.id}>
          <Link
            to={c.to}
            className="group flex h-full items-center gap-3 rounded-2xl bg-surface p-4 shadow-[var(--shadow-card)] ring-1 ring-line/60 transition hover:-translate-y-0.5 hover:shadow-[var(--shadow-lift)] sm:gap-4 sm:p-5"
          >
            <span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-primary-soft text-primary transition group-hover:bg-primary group-hover:text-primary-fg sm:h-14 sm:w-14">
              <Icon name={c.icon} size={26} />
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-bold sm:text-base">{t(`home.shortcuts.${c.id}.title`)}</span>
              <span className="mt-0.5 hidden text-xs text-muted sm:block">{t(`home.shortcuts.${c.id}.text`)}</span>
            </span>
          </Link>
        </li>
      ))}
    </ul>
  )
}

function CategoryTiles() {
  const { t } = useTranslation()
  const { data: categories } = useCategories()
  if (!categories?.length) return null
  return (
    <section aria-labelledby="categories-title">
      <SectionTitle id="categories-title" title={t('home.categories')} />
      <ul className="no-scrollbar -mx-4 flex gap-3 overflow-x-auto px-4 pb-1 sm:mx-0 sm:grid sm:grid-cols-6 sm:overflow-visible sm:px-0 lg:grid-cols-8">
        {categories.slice(0, 16).map((c) => (
          <li key={c.slug} className="w-24 shrink-0 sm:w-auto">
            <Link
              to={`/search?category=${c.slug}`}
              className="group flex h-full flex-col items-center gap-2 rounded-2xl bg-surface px-2 py-4 text-center shadow-[var(--shadow-card)] ring-1 ring-line/60 transition hover:-translate-y-0.5 hover:ring-primary/40"
            >
              <span className="grid h-12 w-12 place-items-center rounded-full bg-primary-soft text-primary transition group-hover:scale-110">
                <CategoryIcon slug={c.slug} size={24} />
              </span>
              <span className="text-xs leading-tight font-semibold">{t(`categories.${c.slug}`, { defaultValue: c.name })}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}

function Newest() {
  const { t } = useTranslation()
  const newest = useListingSearch({ sort: 'newest', limit: '16' })
  const items = newest.data?.pages[0]?.items ?? []
  if (newest.isPending) return <div className="skeleton h-72" />
  if (!items.length) return null
  return (
    <section aria-labelledby="newest-title">
      <SectionTitle id="newest-title" title={t('home.latest')} to="/search?sort=newest" />
      <ListingRail items={items} label={t('home.latest')} />
    </section>
  )
}

export function HomePage() {
  const { t } = useTranslation()
  const { data: me } = useMe()
  const [params, setParams] = useSearchParams()
  const tab = TABS.find((x) => x.id === params.get('tab'))?.id ?? 'trending'

  function select(next: FeedTab) {
    setParams(next === 'trending' ? {} : { tab: next }, { replace: true })
  }
  // Arrow keys move between tabs, as screen reader users expect from a tab list.
  function onKey(e: KeyboardEvent) {
    const i = TABS.findIndex((x) => x.id === tab)
    const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0
    if (!step) return
    e.preventDefault()
    const next = TABS[(i + step + TABS.length) % TABS.length].id
    select(next)
    document.getElementById(`tab-${next}`)?.focus()
  }

  return (
    <div className="space-y-8 sm:space-y-10">
      <div className="space-y-4 sm:space-y-5">
        <HeroCarousel />
        <Shortcuts />
      </div>

      <CategoryTiles />
      <Newest />

      <section aria-labelledby="discover-title" className="space-y-4">
        <h2 id="discover-title" className="text-lg font-extrabold tracking-tight sm:text-xl">{t('home.discover')}</h2>
        <div role="tablist" aria-label={t('feed.label')} className="no-scrollbar flex gap-1 overflow-x-auto border-b border-line" onKeyDown={onKey}>
          {TABS.map((x) => (
            <button
              key={x.id}
              id={`tab-${x.id}`}
              type="button"
              role="tab"
              aria-selected={tab === x.id}
              aria-controls="feed-panel"
              tabIndex={tab === x.id ? 0 : -1}
              onClick={() => select(x.id)}
              className={`-mb-px flex min-h-11 shrink-0 items-center gap-1.5 border-b-2 px-3 text-sm font-semibold whitespace-nowrap transition ${
                tab === x.id ? 'border-primary text-primary' : 'border-transparent text-muted hover:text-fg'
              }`}
            >
              <Icon name={x.icon} size={17} />
              {t(`feed.tabs.${x.id}`)}
            </button>
          ))}
        </div>
        <div id="feed-panel" role="tabpanel" aria-labelledby={`tab-${tab}`}>
          {tab === 'trending' && <Trending />}
          {tab === 'nearby' && <Nearby me={me} />}
          {tab === 'following' && <Following me={me} />}
        </div>
      </section>
    </div>
  )
}
