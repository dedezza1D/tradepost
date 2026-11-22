import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useRef, useState, type ChangeEvent, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import { useLogout, useMe } from '../api/auth'
import { api, json } from '../api/client'
import type { Me } from '../api/types'
import { Avatar } from '../components/Avatar'
import { CityPicker } from '../components/CityPicker'
import { LANGUAGES, currentLanguage } from '../i18n'
import { errorMessage } from '../lib/errors'
import { useChangeLanguage } from '../lib/language'
import { setThemeChoice, themeChoice, type ThemeChoice } from '../theme'
import { Icon, type IconName } from '../ui/icons'
import { NotificationSettings } from '../components/NotificationSettings'

function Row({ to, icon, label }: { to: string; icon: IconName; label: string }) {
  return (
    <li>
      <Link to={to} className="flex min-h-12 items-center gap-3 px-4 hover:bg-surface-2">
        <Icon name={icon} className="text-muted" />
        <span className="flex-1 font-medium">{label}</span>
        <Icon name="chevronRight" size={18} className="text-muted" />
      </Link>
    </li>
  )
}

function Segmented<T extends string>({ name, value, options, onChange }: {
  name: string
  value: T
  options: { value: T; label: string }[]
  onChange: (v: T) => void
}) {
  return (
    <div className="flex rounded-xl bg-surface-2 p-1" role="radiogroup" aria-label={name}>
      {options.map((o) => (
        <label key={o.value} className="flex-1">
          <input type="radio" className="peer sr-only" name={name} checked={value === o.value} onChange={() => onChange(o.value)} />
          <span className="flex min-h-9 cursor-pointer items-center justify-center rounded-lg text-sm font-medium text-muted peer-checked:bg-surface peer-checked:text-fg peer-checked:shadow-sm peer-focus-visible:ring-2 peer-focus-visible:ring-primary">
            {o.label}
          </span>
        </label>
      ))}
    </div>
  )
}

function ProfileForm({ me }: { me: Me }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [name, setName] = useState(me.display_name)
  const [bio, setBio] = useState(me.bio ?? '')
  const [city, setCity] = useState(me.city && me.state ? { ibge_code: me.municipality_id ?? 0, name: me.city, state: me.state } : null)
  const save = useMutation({
    mutationFn: () => api<Me>('/api/auth/me', { method: 'PATCH', body: json({ display_name: name, bio, city: city?.name ?? null, state: city?.state ?? null }) }),
    onSuccess: (updated) => qc.setQueryData(['me'], updated),
  })
  function submit(e: FormEvent) {
    e.preventDefault()
    save.mutate()
  }
  return (
    <form onSubmit={submit} className="space-y-4">
      <div>
        <label className="label" htmlFor="display_name">{t('profile.name')}</label>
        <input id="display_name" className="input" required minLength={2} maxLength={60} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div>
        <label className="label" htmlFor="bio">{t('profile.bio')}</label>
        <textarea id="bio" className="input min-h-24" maxLength={300} value={bio} onChange={(e) => setBio(e.target.value)} placeholder={t('profile.bioPlaceholder')} />
        <p className="mt-1 text-right text-xs text-muted">{bio.length}/300</p>
      </div>
      <CityPicker value={city} onChange={setCity} />
      {save.isError && <p role="alert" className="text-sm text-danger">{errorMessage(save.error, t)}</p>}
      {save.isSuccess && <p className="text-sm text-success">{t('common.saved')}</p>}
      <button className="btn-primary" disabled={save.isPending}>{t('common.save')}</button>
    </form>
  )
}

function AvatarEditor({ me }: { me: Me }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const refresh = () => void qc.invalidateQueries({ queryKey: ['me'] })
  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData()
      form.append('file', file)
      return api<unknown>('/api/me/avatar', { method: 'POST', body: form })
    },
    onSuccess: refresh,
  })
  const remove = useMutation({ mutationFn: () => api<void>('/api/me/avatar', { method: 'DELETE' }), onSuccess: refresh })
  function pick(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (file) upload.mutate(file)
    e.target.value = ''
  }
  return (
    <div className="flex flex-col items-center gap-1">
      <button type="button" className="relative rounded-full focus-visible:ring-2 focus-visible:ring-primary" onClick={() => input.current?.click()} aria-label={t('profile.changePhoto')}>
        <Avatar name={me.display_name} url={me.avatar_url} size="lg" />
        <span className="absolute -right-1 -bottom-1 grid h-7 w-7 place-items-center rounded-full bg-primary text-white ring-2 ring-surface">
          <Icon name="camera" size={15} />
        </span>
      </button>
      <input ref={input} type="file" accept="image/jpeg,image/png,image/webp" className="sr-only" onChange={pick} tabIndex={-1} aria-hidden />
      {me.avatar_url && (
        <button type="button" className="text-xs text-muted hover:text-danger" disabled={remove.isPending} onClick={() => remove.mutate()}>
          {t('profile.removePhoto')}
        </button>
      )}
      {(upload.isError || remove.isError) && <p role="alert" className="text-xs text-danger">{errorMessage(upload.error ?? remove.error, t)}</p>}
    </div>
  )
}

function PasswordForm() {
  const { t } = useTranslation()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const change = useMutation({
    mutationFn: () => api<void>('/api/auth/password', { method: 'POST', body: json({ current_password: current, new_password: next }) }),
    onSuccess: () => { setCurrent(''); setNext('') },
  })
  return (
    <form onSubmit={(e) => { e.preventDefault(); change.mutate() }} className="space-y-4">
      <div>
        <label className="label" htmlFor="current_password">{t('profile.currentPassword')}</label>
        <input id="current_password" type="password" autoComplete="current-password" className="input" required value={current} onChange={(e) => setCurrent(e.target.value)} />
      </div>
      <div>
        <label className="label" htmlFor="new_password">{t('profile.newPassword')}</label>
        <input id="new_password" type="password" autoComplete="new-password" minLength={15} maxLength={128} className="input" required value={next} onChange={(e) => setNext(e.target.value)} />
        <p className="mt-1 text-xs text-muted">{t('auth.passwordHint')}</p>
      </div>
      {change.isError && <p role="alert" className="text-sm text-danger">{errorMessage(change.error, t)}</p>}
      {change.isSuccess && <p className="text-sm text-success">{t('profile.passwordChanged')}</p>}
      <button className="btn-secondary" disabled={change.isPending}>{t('profile.changePassword')}</button>
    </form>
  )
}

export function ProfilePage() {
  const { t } = useTranslation()
  const { data: me } = useMe()
  const logout = useLogout()
  const navigate = useNavigate()
  const [theme, setTheme] = useState<ThemeChoice>(themeChoice())
  const changeLanguage = useChangeLanguage()
  if (!me) return null

  return (
    <div className="mx-auto max-w-xl space-y-6">
      <header className="flex items-center gap-4">
        <AvatarEditor me={me} />
        <div className="min-w-0">
          <h1 className="truncate text-xl font-bold">{me.display_name}</h1>
          <p className="truncate text-sm text-muted">{me.email}</p>
          <Link to={`/users/${me.id}`} className="text-sm font-semibold text-primary hover:underline">{t('profile.publicProfile')}</Link>
        </div>
      </header>

      <ul className="card divide-y divide-line overflow-hidden">
        <Row to="/me/listings" icon="tag" label={t('profile.myListings')} />
        <Row to="/me/saved" icon="heart" label={t('social.savedTitle')} />
        <Row to="/me/following" icon="users" label={t('social.followingTitle')} />
        <Row to="/orders" icon="box" label={t('nav.orders')} />
        <Row to="/seller" icon="store" label={t('nav.selling')} />
        <Row to="/cart" icon="cart" label={t('nav.cart')} />
        <Row to="/me/blocks" icon="ban" label={t('social.blockedTitle')} />
        {me.is_admin && <Row to="/admin/reports" icon="shield" label={t('social.moderation.title')} />}
        {me.is_admin && <Row to="/admin/review" icon="flag" label={t('review.title')} />}
        {me.is_admin && <Row to="/admin/disputes" icon="info" label={t('disputes.adminTitle')} />}
        {me.is_admin && <Row to="/admin/ops" icon="trending" label={t('ops.title')} />}
      </ul>

      <section className="card space-y-4 p-5">
        <h2 className="font-semibold">{t('profile.preferences')}</h2>
        <div className="space-y-2">
          <span className="label">{t('common.language')}</span>
          <Segmented name={t('common.language')} value={currentLanguage()} onChange={(v) => void changeLanguage(v)}
            options={LANGUAGES.map((l) => ({ value: l.code, label: l.label }))} />
        </div>
        <div className="space-y-2">
          <span className="label">{t('profile.theme')}</span>
          <Segmented name={t('profile.theme')} value={theme} onChange={(v) => { setTheme(v); setThemeChoice(v) }}
            options={[{ value: 'system', label: t('profile.themeSystem') }, { value: 'light', label: t('profile.themeLight') }, { value: 'dark', label: t('profile.themeDark') }]} />
        </div>
      </section>

      <NotificationSettings />

      <section className="card space-y-4 p-5">
        <h2 className="font-semibold">{t('profile.details')}</h2>
        <ProfileForm me={me} />
      </section>

      <section className="card space-y-4 p-5">
        <h2 className="font-semibold">{t('profile.security')}</h2>
        <PasswordForm />
      </section>

      <button className="btn-danger w-full" onClick={() => logout.mutate(undefined, { onSuccess: () => navigate('/') })}>
        <Icon name="logout" size={18} />
        {t('nav.signOut')}
      </button>
    </div>
  )
}
