import { useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useMe } from '../api/auth'
import { useCategories } from '../api/catalog'
import { useFeed } from '../api/social'
import type { FeedTab, Me } from '../api/types'
import { CityPicker, type City } from '../components/CityPicker'
import { InfiniteGrid } from '../components/InfiniteGrid'
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

export function HomePage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { data: me } = useMe()
  const { data: categories } = useCategories()
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
    <div className="space-y-6">
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

      <div className="space-y-4">
        <div role="tablist" aria-label={t('feed.label')} className="flex gap-1 border-b border-line" onKeyDown={onKey}>
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
              className={`-mb-px flex min-h-11 items-center gap-1.5 border-b-2 px-3 text-sm font-semibold transition ${
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
      </div>
    </div>
  )
}
