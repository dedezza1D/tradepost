import { useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import { BR_STATES } from '../api/types'
import { useLogin, useRegister } from '../api/auth'

function useReturnTo(): string {
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from
  return from && from.startsWith('/') ? from : '/'
}

function AuthCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-md">
      <div className="card p-6">
        <h1 className="mb-5 text-xl font-semibold">{title}</h1>
        {children}
      </div>
    </div>
  )
}

export function LoginPage() {
  const login = useLogin()
  const navigate = useNavigate()
  const returnTo = useReturnTo()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    login.mutate({ email, password }, { onSuccess: () => navigate(returnTo, { replace: true }) })
  }

  return (
    <AuthCard title="Sign in">
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className="label" htmlFor="email">
            Email
          </label>
          <input id="email" type="email" autoComplete="email" required className="input"
            value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="password">
            Password
          </label>
          <input id="password" type="password" autoComplete="current-password" required
            className="input" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {login.error && (
          <p role="alert" className="text-sm text-red-700">
            {login.error.message}
          </p>
        )}
        <button className="btn-primary w-full" disabled={login.isPending}>
          {login.isPending ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
      <p className="mt-4 text-center text-sm text-stone-600">
        New here?{' '}
        <Link to="/register" className="font-medium text-brand-700 hover:underline">
          Create an account
        </Link>
      </p>
    </AuthCard>
  )
}

export function RegisterPage() {
  const register = useRegister()
  const navigate = useNavigate()
  const returnTo = useReturnTo()
  const [form, setForm] = useState({
    display_name: '',
    email: '',
    password: '',
    city: '',
    state: '',
  })
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm({ ...form, [key]: e.target.value })
  const error = register.error instanceof ApiError ? register.error : null

  function submit(event: FormEvent) {
    event.preventDefault()
    register.mutate(
      {
        email: form.email,
        password: form.password,
        display_name: form.display_name,
        city: form.city || undefined,
        state: form.state || undefined,
      },
      { onSuccess: () => navigate(returnTo, { replace: true }) },
    )
  }

  return (
    <AuthCard title="Create your account">
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className="label" htmlFor="display_name">
            Name shown to buyers
          </label>
          <input id="display_name" required minLength={2} maxLength={60} className="input"
            value={form.display_name} onChange={set('display_name')} />
        </div>
        <div>
          <label className="label" htmlFor="email">
            Email
          </label>
          <input id="email" type="email" autoComplete="email" required className="input"
            value={form.email} onChange={set('email')} />
        </div>
        <div>
          <label className="label" htmlFor="password">
            Password
          </label>
          <input id="password" type="password" autoComplete="new-password" required
            minLength={10} maxLength={128} className="input" value={form.password}
            onChange={set('password')} aria-describedby="password-hint" />
          <p id="password-hint" className="mt-1 text-xs text-stone-500">
            At least 10 characters. A short sentence works well.
          </p>
          {error?.fieldMessage('password') && (
            <p className="mt-1 text-xs text-red-700">{error.fieldMessage('password')}</p>
          )}
        </div>
        <div className="grid grid-cols-[1fr_96px] gap-3">
          <div>
            <label className="label" htmlFor="city">
              City
            </label>
            <input id="city" className="input" maxLength={80} value={form.city}
              onChange={set('city')} />
          </div>
          <div>
            <label className="label" htmlFor="state">
              State
            </label>
            <select id="state" className="input" value={form.state} onChange={set('state')}>
              <option value="">—</option>
              {BR_STATES.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </div>
        </div>
        {error && error.code !== 'validation_error' && (
          <p role="alert" className="text-sm text-red-700">
            {error.message}
          </p>
        )}
        <button className="btn-primary w-full" disabled={register.isPending}>
          {register.isPending ? 'Creating account…' : 'Create account'}
        </button>
      </form>
      <p className="mt-4 text-center text-sm text-stone-600">
        Already have an account?{' '}
        <Link to="/login" className="font-medium text-brand-700 hover:underline">
          Sign in
        </Link>
      </p>
    </AuthCard>
  )
}
