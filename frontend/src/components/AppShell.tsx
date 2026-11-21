import { useEffect, useRef, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useMe } from '../api/auth'
import { useCategories } from '../api/catalog'
import { useUnreadChats } from '../api/chat'
import { useCart } from '../api/orders'
import { currentLanguage } from '../i18n'
import { CategoryIcon } from '../ui/categoryIcons'
import { Icon, type IconName } from '../ui/icons'
import { BrandMark, Logo } from './Brand'
import { NotificationBell } from './NotificationBell'
import { SearchBox } from './SearchBox'

const onBand = 'rounded-full p-2 hover:bg-white/15'

function HeaderSearch({ className }: { className: string }) {
  const navigate = useNavigate()
  const [q, setQ] = useState('')
  return (
    <SearchBox
      id={`header-search-${className.includes('md:hidden') ? 'phone' : 'desktop'}`}
      className={className}
      value={q}
      onChange={setQ}
      onSearch={(query) => navigate(query ? `/search?q=${encodeURIComponent(query)}` : '/search')}
    />
  )
}

function CountBadge({ count, label }: { count: number | undefined; label?: string }) {
  if (!count) return null
  return (
    <span className="absolute -top-0.5 -right-0.5 min-w-5 rounded-full bg-white px-1 text-center text-[11px] leading-5 font-bold text-[#c8102e]">
      {label && <span className="sr-only">{label}</span>}
      <span aria-hidden={Boolean(label)}>{count}</span>
    </span>
  )
}

function CartLink() {
  const { t } = useTranslation()
  const { data: me } = useMe()
  const { data: cart } = useCart(Boolean(me))
  const count = cart?.items.reduce((n, i) => n + i.quantity, 0) ?? 0
  if (!me) return null
  return (
    <Link to="/cart" className={`relative ${onBand}`} aria-label={count ? t('nav.cartCount', { count }) : t('nav.cart')}>
      <Icon name="cart" />
      <CountBadge count={count} />
    </Link>
  )
}

/** Where listings are measured from: the signed-in person's city, or a prompt to choose one. */
function ShipTo() {
  const { t } = useTranslation()
  const { data: me } = useMe()
  const city = me?.city ? `${me.city} - ${me.state}` : null
  return (
    <Link to="/?tab=nearby" className="flex min-w-0 items-center gap-1.5 rounded-lg py-1 pr-2 hover:bg-white/10">
      <Icon name="pin" size={20} className="shrink-0 opacity-90" />
      <span className="min-w-0 leading-tight">
        <span className="block text-[11px] opacity-80">{t('nav.shipTo')}</span>
        <span className="block truncate text-sm font-semibold">{city ?? t('nav.chooseCity')}</span>
      </span>
    </Link>
  )
}

/** Disclosure menu with every category; closes on Escape, outside clicks and navigation. */
function CategoriesMenu() {
  const { t } = useTranslation()
  const { data: categories } = useCategories()
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const location = useLocation()
  useEffect(() => setOpen(false), [location])
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false)
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', esc)
    }
  }, [open])
  return (
    <div className="relative" ref={box}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls="categories-menu"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 rounded-lg px-2 py-1.5 font-semibold whitespace-nowrap hover:bg-white/10"
      >
        {t('home.categories')}
        <Icon name="chevronDown" size={16} className={`transition ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div id="categories-menu" className="card absolute top-full left-0 z-30 mt-2 w-[34rem] p-2 text-fg shadow-[var(--shadow-lift)]">
          <ul className="grid grid-cols-2 gap-0.5">
            {categories?.map((c) => (
              <li key={c.slug}>
                <Link to={`/search?category=${c.slug}`} className="flex items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium hover:bg-surface-2">
                  <span className="grid h-8 w-8 place-items-center rounded-lg bg-primary-soft text-primary">
                    <CategoryIcon slug={c.slug} size={18} />
                  </span>
                  {t(`categories.${c.slug}`, { defaultValue: c.name })}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function SubNav() {
  const { t } = useTranslation()
  const { data: me } = useMe()
  const { data: unread } = useUnreadChats()
  const link = ({ isActive }: { isActive: boolean }) =>
    `relative rounded-lg px-2 py-1.5 whitespace-nowrap hover:bg-white/10 ${isActive ? 'font-bold underline decoration-2 underline-offset-[6px]' : 'font-medium'}`
  return (
    <div className="hidden h-11 items-center gap-4 text-sm md:flex lg:gap-6">
      <div className="w-40 shrink-0 lg:w-48">
        <ShipTo />
      </div>
      <nav aria-label={t('nav.main')} className="flex items-center gap-2">
        <CategoriesMenu />
        <NavLink to="/?tab=nearby" className={link({ isActive: false })}>{t('feed.tabs.nearby')}</NavLink>
        <NavLink to="/?tab=following" className={link({ isActive: false })}>{t('feed.tabs.following')}</NavLink>
        <NavLink to={me ? '/sell' : '/login'} className={link}>{t('nav.sell')}</NavLink>
      </nav>
      {me && (
        <nav aria-label={t('nav.account')} className="ml-auto hidden items-center gap-2 lg:flex">
          <NavLink to="/orders" className={link}>{t('nav.orders')}</NavLink>
          <NavLink to="/inbox" className={link}>
            {t('nav.messages')}
            {Boolean(unread) && (
              <span className="ml-1.5 rounded-full bg-white px-1.5 text-[11px] font-bold text-[#c8102e]">
                <span className="sr-only">{t('chat.unreadConversations', { count: unread })}</span>
                <span aria-hidden>{unread}</span>
              </span>
            )}
          </NavLink>
          <NavLink to="/me/saved" className={link}>{t('nav.saved')}</NavLink>
          <NavLink to="/seller" className={link}>{t('nav.selling')}</NavLink>
        </nav>
      )}
    </div>
  )
}

function HeaderActions() {
  const { t } = useTranslation()
  const { data: me } = useMe()
  if (!me) {
    return (
      <div className="flex items-center gap-1 sm:gap-2">
        <Link to="/login" className="rounded-full px-3 py-2 text-sm font-semibold hover:bg-white/15">
          {t('nav.signIn')}
        </Link>
        <Link to="/register" className="hidden rounded-full bg-white px-4 py-2 text-sm font-bold text-[#c8102e] shadow-sm hover:bg-white/90 sm:inline-flex">
          {t('nav.createAccount')}
        </Link>
      </div>
    )
  }
  return (
    <div className="flex items-center gap-0.5 sm:gap-1">
      <Link to="/sell" className="mr-1 hidden items-center gap-1.5 rounded-full bg-white px-4 py-2 text-sm font-bold text-[#c8102e] shadow-sm hover:bg-white/90 md:inline-flex">
        <Icon name="plus" size={18} />
        {t('nav.sell')}
      </Link>
      <CartLink />
      <NotificationBell />
      <Link
        to="/me"
        className="ml-1 hidden h-9 w-9 place-items-center rounded-full bg-white/20 text-sm font-bold ring-2 ring-white/40 hover:bg-white/30 md:grid"
        aria-label={t('nav.profile')}
      >
        {me.display_name.slice(0, 1).toUpperCase()}
      </Link>
    </div>
  )
}

function BottomNav() {
  const { t } = useTranslation()
  const { data: me } = useMe()
  const { data: unread } = useUnreadChats()
  const items: { to: string; icon: IconName; label: string; end?: boolean; badge?: number }[] = [
    { to: '/', icon: 'home', label: t('nav.home'), end: true },
    { to: '/search', icon: 'search', label: t('nav.search') },
    { to: me ? '/sell' : '/login', icon: 'plus', label: t('nav.sell') },
    // Alerts stay one tap away in the header bell; messages get the tab.
    { to: me ? '/inbox' : '/login', icon: 'chat', label: t('nav.messages'), badge: unread },
    { to: me ? '/me' : '/login', icon: 'user', label: t('nav.profile') },
  ]
  return (
    <nav
      aria-label={t('nav.tabs')}
      className="safe-bottom fixed inset-x-0 bottom-0 z-30 border-t border-line bg-surface/95 shadow-[0_-4px_16px_-8px_rgb(60_20_10/0.15)] backdrop-blur md:hidden"
    >
      <ul className="mx-auto grid max-w-md grid-cols-5">
        {items.map((item, i) => (
          <li key={item.label}>
            <NavLink
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `flex flex-col items-center gap-0.5 py-2 text-[11px] font-semibold ${isActive ? 'text-primary' : 'text-muted'}`
              }
            >
              <span className="grid h-8 place-items-center">
                {i === 2 ? (
                  <span className="band grid h-9 w-12 place-items-center rounded-2xl shadow-[var(--shadow-card)]">
                    <Icon name="plus" size={22} />
                  </span>
                ) : (
                  <span className="relative">
                    <Icon name={item.icon} />
                    {item.badge ? (
                      <span className="absolute -top-1 -right-2 min-w-4.5 rounded-full bg-primary px-1 text-center text-[10px] leading-4.5 font-bold text-primary-fg">
                        <span className="sr-only">{t('chat.unreadConversations', { count: item.badge })}</span>
                        <span aria-hidden>{item.badge}</span>
                      </span>
                    ) : null}
                  </span>
                )}
              </span>
              {item.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  )
}

function Footer() {
  const { t } = useTranslation()
  const { data: categories } = useCategories()
  const columns: { title: string; links: { label: string; to: string }[] }[] = [
    {
      title: t('footer.buy'),
      links: [
        { label: t('feed.tabs.trending'), to: '/' },
        { label: t('feed.tabs.nearby'), to: '/?tab=nearby' },
        { label: t('nav.saved'), to: '/me/saved' },
        { label: t('nav.orders'), to: '/orders' },
      ],
    },
    {
      title: t('footer.sell'),
      links: [
        { label: t('footer.listItem'), to: '/sell' },
        { label: t('nav.selling'), to: '/seller' },
        { label: t('footer.reputation'), to: '/help/reputation' },
      ],
    },
    {
      title: t('home.categories'),
      links: (categories ?? []).slice(0, 5).map((c) => ({
        label: t(`categories.${c.slug}`, { defaultValue: c.name }),
        to: `/search?category=${c.slug}`,
      })),
    },
  ]
  return (
    <footer className="mt-8 hidden border-t border-line bg-surface md:block">
      <div className="mx-auto grid max-w-6xl grid-cols-[1.4fr_1fr_1fr_1fr] gap-8 px-4 py-10">
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-lg tracking-tight">
            <BrandMark size={30} />
            <span>
              <span className="font-extrabold">trade</span>
              <span className="font-medium">post</span>
            </span>
          </div>
          <p className="max-w-xs text-sm text-muted">{t('footer.pitch')}</p>
          <ul className="space-y-1.5 pt-1 text-sm">
            {(['shield', 'truck', 'chat'] as const).map((icon, i) => (
              <li key={icon} className="flex items-center gap-2 text-muted">
                <Icon name={icon} size={16} className="text-primary" />
                {t(`footer.promise.${i}`)}
              </li>
            ))}
          </ul>
        </div>
        {columns.map((col) => (
          <div key={col.title}>
            <h2 className="mb-3 text-sm font-bold">{col.title}</h2>
            <ul className="space-y-2 text-sm">
              {col.links.map((l) => (
                <li key={l.to}>
                  <Link to={l.to} className="text-muted hover:text-primary">{l.label}</Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <div className="border-t border-line">
        <p className="mx-auto max-w-6xl px-4 py-4 text-xs text-muted">{t('nav.footer')}</p>
      </div>
    </footer>
  )
}

export function AppShell() {
  const { t, i18n } = useTranslation()
  const { data: me } = useMe()
  const location = useLocation()

  // A signed-in user's saved language wins over this browser's default.
  useEffect(() => {
    if (me?.locale && me.locale !== currentLanguage()) void i18n.changeLanguage(me.locale)
  }, [me?.locale, i18n])
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [location.pathname])

  return (
    <div className="flex min-h-dvh flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:m-2 focus:rounded-lg focus:bg-surface focus:p-2">
        {t('nav.skip')}
      </a>
      <div className="bg-fg px-4 py-1 text-center text-[11px] text-bg sm:text-xs">
        <Trans i18nKey="nav.demoBanner" components={{ strong: <strong /> }} />
      </div>
      <header className="band sticky top-0 z-20 shadow-[0_2px_12px_-4px_rgb(90_10_20/0.35)]">
        <div className="mx-auto max-w-6xl px-4">
          <div className="flex h-14 items-center gap-3 md:h-16 md:gap-6">
            <Logo />
            <HeaderSearch className="hidden max-w-2xl flex-1 md:block" />
            <div className="ml-auto">
              <HeaderActions />
            </div>
          </div>
          <HeaderSearch className="pb-3 md:hidden" />
          <SubNav />
        </div>
      </header>
      {location.pathname === '/' && (
        // The band runs on behind the top of the home page and fades into the background.
        <div aria-hidden className="-mb-56 h-56 bg-[linear-gradient(180deg,var(--tp-band-2)_0%,var(--tp-bg)_100%)] md:-mb-72 md:h-72" />
      )}
      <main id="main" className="relative mx-auto w-full max-w-6xl flex-1 px-4 pt-4 pb-28 md:pt-6 md:pb-10">
        <Outlet />
      </main>
      <Footer />
      <BottomNav />
    </div>
  )
}
