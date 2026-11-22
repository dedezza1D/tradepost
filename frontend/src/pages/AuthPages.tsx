import { useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useLogin, useRegister } from '../api/auth'
import { ApiError } from '../api/client'
import { CityPicker, type City } from '../components/CityPicker'
import { currentLanguage } from '../i18n'
import { errorMessage } from '../lib/errors'

function useReturnTo(): string {
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from
  return from && from.startsWith('/') ? from : '/'
}

function AuthCard({ title, subtitle, children, footer }: { title: string; subtitle: string; children: ReactNode; footer: ReactNode }) {
  return (
    <div className="mx-auto max-w-md py-4">
      <div className="mb-6 text-center">
        <img src="/favicon.svg" alt="" className="mx-auto mb-3 h-12 w-12" />
        <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
        <p className="mt-1 text-sm text-muted">{subtitle}</p>
      </div>
      <div className="card p-5 sm:p-6">{children}</div>
      <p className="mt-5 text-center text-sm text-muted">{footer}</p>
    </div>
  )
}

export function LoginPage() {
  const { t } = useTranslation()
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
    <AuthCard
      title={t('auth.signInTitle')}
      subtitle={t('auth.signInSubtitle')}
      footer={<>{t('auth.newHere')} <Link to="/register" className="font-semibold text-primary hover:underline">{t('nav.createAccount')}</Link></>}
    >
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className="label" htmlFor="email">{t('auth.email')}</label>
          <input id="email" type="email" autoComplete="email" inputMode="email" required className="input" value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="password">{t('auth.password')}</label>
          <input id="password" type="password" autoComplete="current-password" required className="input" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {login.error && <p role="alert" className="text-sm text-danger">{errorMessage(login.error, t)}</p>}
        <button className="btn-primary w-full" disabled={login.isPending}>
          {login.isPending ? t('auth.signingIn') : t('nav.signIn')}
        </button>
      </form>
    </AuthCard>
  )
}

export function RegisterPage() {
  const { t } = useTranslation()
  const register = useRegister()
  const navigate = useNavigate()
  const returnTo = useReturnTo()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [city, setCity] = useState<City | null>(null)
  const error = register.error instanceof ApiError ? register.error : null

  function submit(event: FormEvent) {
    event.preventDefault()
    register.mutate(
      { email, password, display_name: name, city: city?.name, state: city?.state, locale: currentLanguage() },
      { onSuccess: () => navigate(returnTo, { replace: true }) },
    )
  }

  return (
    <AuthCard
      title={t('auth.registerTitle')}
      subtitle={t('auth.registerSubtitle')}
      footer={<>{t('auth.haveAccount')} <Link to="/login" className="font-semibold text-primary hover:underline">{t('nav.signIn')}</Link></>}
    >
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className="label" htmlFor="display_name">{t('auth.displayName')}</label>
          <input id="display_name" required minLength={2} maxLength={60} autoComplete="name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="email">{t('auth.email')}</label>
          <input id="email" type="email" autoComplete="email" inputMode="email" required className="input" value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="password">{t('auth.password')}</label>
          <input id="password" type="password" autoComplete="new-password" required minLength={15} maxLength={128} className="input"
            aria-describedby="password-hint" value={password} onChange={(e) => setPassword(e.target.value)} />
          <p id="password-hint" className="mt-1 text-xs text-muted">{t('auth.passwordHint')}</p>
          {error?.fieldMessage('password') && <p className="mt-1 text-xs text-danger">{t('auth.passwordTooShort')}</p>}
        </div>
        <CityPicker value={city} onChange={setCity} />
        <p className="text-xs text-muted">{t('auth.cityWhy')}</p>
        {error && error.code !== 'validation_error' && <p role="alert" className="text-sm text-danger">{errorMessage(error, t)}</p>}
        <button className="btn-primary w-full" disabled={register.isPending}>
          {register.isPending ? t('auth.creating') : t('nav.createAccount')}
        </button>
      </form>
    </AuthCard>
  )
}
