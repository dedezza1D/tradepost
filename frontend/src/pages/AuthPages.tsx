import { useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useLogin, useRegister, useRequestPasswordReset, useResetPassword } from '../api/auth'
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
  const location = useLocation()
  const returnTo = useReturnTo()
  const passwordReset = (location.state as { passwordReset?: boolean } | null)?.passwordReset === true
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
        {passwordReset && !login.error && <p role="status" className="rounded-2xl bg-success-soft px-3 py-2 text-sm text-success">{t('auth.passwordResetDone')}</p>}
        <div>
          <label className="label" htmlFor="email">{t('auth.email')}</label>
          <input id="email" type="email" autoComplete="email" inputMode="email" required className="input" value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="password">{t('auth.password')}</label>
          <input id="password" type="password" autoComplete="current-password" required className="input" value={password} onChange={(e) => setPassword(e.target.value)} />
          <p className="mt-1.5 text-right text-sm">
            <Link to="/forgot-password" state={{ email }} className="font-semibold text-primary hover:underline">{t('auth.forgotPassword')}</Link>
          </p>
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

export function ForgotPasswordPage() {
  const { t } = useTranslation()
  const location = useLocation()
  const request = useRequestPasswordReset()
  const [email, setEmail] = useState(() => (location.state as { email?: string } | null)?.email ?? '')

  function submit(event: FormEvent) {
    event.preventDefault()
    request.mutate({ email })
  }

  const backToSignIn = <Link to="/login" className="font-semibold text-primary hover:underline">{t('auth.backToSignIn')}</Link>

  if (request.isSuccess) {
    return (
      <AuthCard title={t('auth.linkSentTitle')} subtitle={t('auth.forgotTitle')} footer={backToSignIn}>
        <div role="status" className="space-y-3 text-sm">
          <p>{t('auth.linkSent', { email: request.variables.email })}</p>
          <p className="text-muted">{t('auth.linkSentHelp')}</p>
        </div>
        <button type="button" className="btn-secondary mt-5 w-full" onClick={() => request.reset()}>{t('auth.askAgain')}</button>
      </AuthCard>
    )
  }

  return (
    <AuthCard title={t('auth.forgotTitle')} subtitle={t('auth.forgotSubtitle')} footer={backToSignIn}>
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className="label" htmlFor="email">{t('auth.email')}</label>
          <input id="email" type="email" autoComplete="email" inputMode="email" required className="input" value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        {request.error && <p role="alert" className="text-sm text-danger">{errorMessage(request.error, t)}</p>}
        <button className="btn-primary w-full" disabled={request.isPending}>
          {request.isPending ? t('auth.sending') : t('auth.sendLink')}
        </button>
      </form>
    </AuthCard>
  )
}

/** The page the e-mailed link opens. The token travels in the URL fragment, which the browser
 * never sends to a server. */
export function ResetPasswordPage() {
  const { t } = useTranslation()
  const location = useLocation()
  const navigate = useNavigate()
  const resetPassword = useResetPassword()
  const [password, setPassword] = useState('')
  const token = new URLSearchParams(location.hash.slice(1)).get('token') ?? ''
  const error = resetPassword.error instanceof ApiError ? resetPassword.error : null
  const linkDead = !token || error?.code === 'reset_link_invalid'

  function submit(event: FormEvent) {
    event.preventDefault()
    resetPassword.mutate(
      { token, new_password: password },
      { onSuccess: () => navigate('/login', { replace: true, state: { passwordReset: true } }) },
    )
  }

  const footer = <Link to="/login" className="font-semibold text-primary hover:underline">{t('auth.backToSignIn')}</Link>

  if (linkDead) {
    return (
      <AuthCard title={t('auth.resetTitle')} subtitle={t('auth.forgotSubtitle')} footer={footer}>
        <p role="alert" className="text-sm text-danger">{token ? errorMessage(error, t) : t('auth.resetLinkMissing')}</p>
        <Link to="/forgot-password" className="btn-primary mt-5 w-full">{t('auth.askAgain')}</Link>
      </AuthCard>
    )
  }

  return (
    <AuthCard title={t('auth.resetTitle')} subtitle={t('auth.resetSubtitle')} footer={footer}>
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className="label" htmlFor="new_password">{t('auth.newPassword')}</label>
          <input id="new_password" type="password" autoComplete="new-password" required minLength={15} maxLength={128} className="input"
            aria-describedby="password-hint" value={password} onChange={(e) => setPassword(e.target.value)} />
          <p id="password-hint" className="mt-1 text-xs text-muted">{t('auth.passwordHint')}</p>
          {error?.fieldMessage('new_password') && <p className="mt-1 text-xs text-danger">{t('auth.passwordTooShort')}</p>}
        </div>
        {error && error.code !== 'validation_error' && <p role="alert" className="text-sm text-danger">{errorMessage(error, t)}</p>}
        <button className="btn-primary w-full" disabled={resetPassword.isPending}>
          {resetPassword.isPending ? t('auth.saving') : t('auth.savePassword')}
        </button>
      </form>
    </AuthCard>
  )
}
