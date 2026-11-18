import { useEffect, useState, type FormEvent } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { Link, NavLink, Outlet, useNavigate, useSearchParams } from 'react-router-dom'
import { useLogout, useMe } from '../api/auth'
import { useCart } from '../api/orders'
import { currentLanguage } from '../i18n'
import { LanguageSwitcher } from './LanguageSwitcher'
import { NotificationBell } from './NotificationBell'

function SearchBox() {
  const { t } = useTranslation()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const [q, setQ] = useState(params.get('q') ?? '')

  function submit(event: FormEvent) {
    event.preventDefault()
    const next = new URLSearchParams()
    if (q.trim()) next.set('q', q.trim())
    navigate(`/?${next}`)
  }

  return (
    <form onSubmit={submit} role="search" className="flex flex-1">
      <label htmlFor="site-search" className="sr-only">
        {t('nav.searchLabel')}
      </label>
      <input
        id="site-search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder={t('nav.searchPlaceholder')}
        className="input rounded-r-none"
        maxLength={100}
      />
      <button type="submit" className="btn-primary rounded-l-none">
        {t('nav.search')}
      </button>
    </form>
  )
}

function AccountMenu() {
  const { t, i18n } = useTranslation()
  const { data: me } = useMe()
  const { data: cart } = useCart(Boolean(me))
  const count = cart?.items.reduce((n, i) => n + i.quantity, 0) ?? 0
  const logout = useLogout()
  const navigate = useNavigate()

  // A signed-in user's saved language wins over this browser's default.
  useEffect(() => {
    if (me?.locale && me.locale !== currentLanguage()) void i18n.changeLanguage(me.locale)
  }, [me?.locale, i18n])

  if (!me) {
    return (
      <div className="flex items-center gap-2">
        <LanguageSwitcher />
        <Link to="/login" className="btn-secondary">
          {t('nav.signIn')}
        </Link>
        <Link to="/register" className="btn-primary hidden sm:inline-flex">
          {t('nav.createAccount')}
        </Link>
      </div>
    )
  }
  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `rounded-md px-2 py-1 text-sm font-medium ${isActive ? 'text-brand-700' : 'text-stone-700 hover:text-stone-950'}`
  return (
    <nav className="flex items-center gap-1 sm:gap-2" aria-label={t('nav.account')}>
      <NavLink to="/seller" className={linkClass}>
        {t('nav.selling')}
      </NavLink>
      <NavLink to="/orders" className={linkClass}>
        {t('nav.orders')}
      </NavLink>
      <NavLink to="/cart" className={linkClass}>
        {t('nav.cart')}
        {count ? ` (${count})` : ''}
      </NavLink>
      <NotificationBell />
      <LanguageSwitcher />
      <button
        type="button"
        className="btn-secondary px-3 py-1.5"
        onClick={() => logout.mutate(undefined, { onSuccess: () => navigate('/') })}
      >
        {t('nav.signOut')}
      </button>
    </nav>
  )
}

export function Layout() {
  const { t } = useTranslation()
  return (
    <div className="flex min-h-screen flex-col">
      <div className="bg-amber-100 px-4 py-1.5 text-center text-xs text-amber-900">
        <Trans i18nKey="nav.demoBanner" components={{ strong: <strong /> }} />
      </div>
      <header className="sticky top-0 z-10 border-b border-stone-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-3 px-4 py-3">
          <Link to="/" className="flex items-center gap-2 text-lg font-bold tracking-tight">
            <img src="/favicon.svg" alt="" className="h-7 w-7" />
            tradepost
          </Link>
          <div className="order-last w-full sm:order-none sm:w-auto sm:flex-1">
            <SearchBox />
          </div>
          <div className="ml-auto">
            <AccountMenu />
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">
        <Outlet />
      </main>
      <footer className="border-t border-stone-200 py-6 text-center text-xs text-stone-500">
        {t('nav.footer')}
      </footer>
    </div>
  )
}
