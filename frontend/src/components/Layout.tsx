import { useState, type FormEvent } from 'react'
import { Link, NavLink, Outlet, useNavigate, useSearchParams } from 'react-router-dom'
import { useLogout, useMe } from '../api/auth'
import { useCart } from '../api/orders'

function SearchBox() {
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
        Search listings
      </label>
      <input
        id="site-search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search for bikes, phones, books…"
        className="input rounded-r-none"
        maxLength={100}
      />
      <button type="submit" className="btn-primary rounded-l-none">
        Search
      </button>
    </form>
  )
}

function AccountMenu() {
  const { data: me } = useMe()
  const { data: cart } = useCart(Boolean(me))
  const count = cart?.items.reduce((n, i) => n + i.quantity, 0) ?? 0
  const logout = useLogout()
  const navigate = useNavigate()

  if (!me) {
    return (
      <div className="flex items-center gap-2">
        <Link to="/login" className="btn-secondary">
          Sign in
        </Link>
        <Link to="/register" className="btn-primary hidden sm:inline-flex">
          Create account
        </Link>
      </div>
    )
  }
  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `rounded-md px-2 py-1 text-sm font-medium ${isActive ? 'text-brand-700' : 'text-stone-700 hover:text-stone-950'}`
  return (
    <nav className="flex items-center gap-1 sm:gap-3" aria-label="Account">
      <NavLink to="/seller" className={linkClass}>
        Selling
      </NavLink>
      <NavLink to="/orders" className={linkClass}>
        Orders
      </NavLink>
      <NavLink to="/cart" className={linkClass}>
        Cart{count ? ` (${count})` : ''}
      </NavLink>
      <span className="hidden text-sm text-stone-500 md:inline">{me.display_name}</span>
      <button
        type="button"
        className="btn-secondary px-3 py-1.5"
        onClick={() => logout.mutate(undefined, { onSuccess: () => navigate('/') })}
      >
        Sign out
      </button>
    </nav>
  )
}

export function Layout() {
  return (
    <div className="flex min-h-screen flex-col">
      <div className="bg-amber-100 px-4 py-1.5 text-center text-xs text-amber-900">
        Demo marketplace. Payments run in <strong>test mode</strong>: no real money moves.
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
        tradepost is a portfolio project. Listings and payments are for demonstration only.
      </footer>
    </div>
  )
}
