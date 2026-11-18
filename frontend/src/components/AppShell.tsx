import { useState, type FormEvent } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useMe } from '../api/auth'
import { useUnreadChats } from '../api/chat'
import { useCart } from '../api/orders'
import { currentLanguage } from '../i18n'
import { Icon, type IconName } from '../ui/icons'
import { NotificationBell } from './NotificationBell'
import { useEffect } from 'react'

function Logo() {
  return (
    <Link to="/" className="flex items-center gap-2 text-lg font-extrabold tracking-tight" aria-label="tradepost">
      <img src="/favicon.svg" alt="" className="h-8 w-8" />
      <span className="hidden min-[380px]:inline">tradepost</span>
    </Link>
  )
}

function HeaderSearch() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [q, setQ] = useState('')
  function submit(event: FormEvent) {
    event.preventDefault()
    navigate(q.trim() ? `/search?q=${encodeURIComponent(q.trim())}` : '/search')
  }
  return (
    <form onSubmit={submit} role="search" className="relative hidden max-w-xl flex-1 md:block">
      <label htmlFor="header-search" className="sr-only">
        {t('nav.searchLabel')}
      </label>
      <Icon name="search" size={18} className="pointer-events-none absolute top-1/2 left-3.5 -translate-y-1/2 text-muted" />
      <input
        id="header-search"
        className="input rounded-full pl-10"
        placeholder={t('nav.searchPlaceholder')}
        maxLength={100}
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
    </form>
  )
}

function CartLink() {
  const { t } = useTranslation()
  const { data: me } = useMe()
  const { data: cart } = useCart(Boolean(me))
  const count = cart?.items.reduce((n, i) => n + i.quantity, 0) ?? 0
  if (!me) return null
  return (
    <Link
      to="/cart"
      className="relative rounded-full p-2 text-fg hover:bg-surface-2"
      aria-label={count ? t('nav.cartCount', { count }) : t('nav.cart')}
    >
      <Icon name="cart" />
      {count > 0 && (
        <span className="absolute -top-0.5 -right-0.5 min-w-5 rounded-full bg-accent px-1 text-center text-[11px] leading-5 font-bold text-white">
          {count}
        </span>
      )}
    </Link>
  )
}

function UnreadDot({ count }: { count: number | undefined }) {
  const { t } = useTranslation()
  if (!count) return null
  return (
    <span className="absolute -top-1 -right-2 min-w-4.5 rounded-full bg-accent px-1 text-center text-[10px] leading-4.5 font-bold text-white">
      <span className="sr-only">{t('chat.unreadConversations', { count })}</span>
      <span aria-hidden>{count}</span>
    </span>
  )
}

function DesktopNav() {
  const { t } = useTranslation()
  const { data: me } = useMe()
  const { data: unread } = useUnreadChats()
  const link = ({ isActive }: { isActive: boolean }) =>
    `rounded-full px-3 py-2 text-sm font-medium ${isActive ? 'bg-primary-soft text-primary' : 'text-fg hover:bg-surface-2'}`
  if (!me) {
    return (
      <div className="hidden items-center gap-2 md:flex">
        <Link to="/login" className="btn-ghost">
          {t('nav.signIn')}
        </Link>
        <Link to="/register" className="btn-primary">
          {t('nav.createAccount')}
        </Link>
      </div>
    )
  }
  return (
    <nav className="hidden items-center gap-1 md:flex" aria-label={t('nav.account')}>
      <NavLink to="/inbox" className={link}>
        <span className="relative">
          {t('nav.messages')}
          <UnreadDot count={unread} />
        </span>
      </NavLink>
      <NavLink to="/me/saved" className={link}>
        {t('nav.saved')}
      </NavLink>
      <NavLink to="/orders" className={link}>
        {t('nav.orders')}
      </NavLink>
      <NavLink to="/seller" className={link}>
        {t('nav.selling')}
      </NavLink>
      <Link to="/sell" className="btn-primary ml-1 min-h-10 rounded-full">
        <Icon name="plus" size={18} />
        {t('nav.sell')}
      </Link>
    </nav>
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
      aria-label={t('nav.main')}
      className="safe-bottom fixed inset-x-0 bottom-0 z-30 border-t border-line bg-surface/95 backdrop-blur md:hidden"
    >
      <ul className="mx-auto grid max-w-md grid-cols-5">
        {items.map((item, i) => (
          <li key={item.label}>
            <NavLink
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `flex flex-col items-center gap-0.5 py-2 text-[11px] font-medium ${isActive ? 'text-primary' : 'text-muted'}`
              }
            >
              <span className="grid h-8 place-items-center">
                {i === 2 ? (
                  <span className="grid h-8 w-12 place-items-center rounded-2xl bg-primary text-primary-fg shadow-sm">
                    <Icon name="plus" size={22} />
                  </span>
                ) : (
                  <span className="relative">
                    <Icon name={item.icon} />
                    <UnreadDot count={item.badge} />
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
      <div className="bg-warning-soft px-4 py-1 text-center text-[11px] text-warning-fg sm:text-xs">
        <Trans i18nKey="nav.demoBanner" components={{ strong: <strong /> }} />
      </div>
      <header className="sticky top-0 z-20 border-b border-line bg-surface/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-4 md:h-16">
          <Logo />
          <HeaderSearch />
          <div className="ml-auto flex items-center gap-1">
            <DesktopNav />
            <CartLink />
            {me && <NotificationBell />}
            {me && (
              <Link
                to="/me"
                className="hidden h-9 w-9 place-items-center rounded-full bg-primary-soft text-sm font-bold text-primary md:grid"
                aria-label={t('nav.profile')}
              >
                {me.display_name.slice(0, 1).toUpperCase()}
              </Link>
            )}
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 pt-4 pb-28 md:pt-6 md:pb-10">
        <Outlet />
      </main>
      <footer className="hidden border-t border-line py-6 text-center text-xs text-muted md:block">
        {t('nav.footer')}
      </footer>
      <BottomNav />
    </div>
  )
}
