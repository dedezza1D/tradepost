import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import {
  useMfaConfirm,
  useMfaDisable,
  useMfaSetup,
  useMfaStatus,
  useNewRecoveryCodes,
  type MfaSetup,
} from '../api/mfa'
import { errorMessage } from '../lib/errors'

type Mode = 'idle' | 'enable' | 'disable' | 'codes'

/** Two-step verification with an authenticator app, and its recovery codes. */
export function TwoFactorSettings() {
  const { t } = useTranslation()
  const status = useMfaStatus()
  const [mode, setMode] = useState<Mode>('idle')
  const [codes, setCodes] = useState<string[] | null>(null)

  if (!status.data) return null
  const { enabled, recovery_codes_left: left } = status.data
  const finish = () => { setMode('idle'); setCodes(null) }

  return (
    <section className="card space-y-4 p-5" aria-labelledby="mfa-title">
      <div className="space-y-1">
        <h2 id="mfa-title" className="font-semibold">{t('mfa.title')}</h2>
        <p className="text-sm text-muted">{enabled ? t('mfa.on', { count: left }) : t('mfa.off')}</p>
      </div>

      {codes ? (
        <RecoveryCodes codes={codes} onDone={finish} />
      ) : mode === 'enable' ? (
        <EnableFlow onCodes={setCodes} onCancel={finish} />
      ) : mode === 'disable' ? (
        <DisableForm onDone={finish} />
      ) : mode === 'codes' ? (
        <NewCodesForm onCodes={setCodes} onCancel={finish} />
      ) : enabled ? (
        <div className="flex flex-wrap gap-2">
          <button className="btn-secondary" onClick={() => setMode('codes')}>{t('mfa.newCodes')}</button>
          <button className="btn-secondary" onClick={() => setMode('disable')}>{t('mfa.turnOff')}</button>
        </div>
      ) : (
        <button className="btn-secondary" onClick={() => setMode('enable')}>{t('mfa.turnOn')}</button>
      )}
    </section>
  )
}

function PasswordField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { t } = useTranslation()
  return (
    <div>
      <label className="label" htmlFor="mfa-password">{t('mfa.password')}</label>
      <input id="mfa-password" type="password" autoComplete="current-password" required className="input"
        value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  )
}

function EnableFlow({ onCodes, onCancel }: { onCodes: (codes: string[]) => void; onCancel: () => void }) {
  const { t } = useTranslation()
  const setup = useMfaSetup()
  const confirm = useMfaConfirm()
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [pending, setPending] = useState<MfaSetup | null>(null)

  if (!pending) {
    return (
      <form className="space-y-4" onSubmit={(e: FormEvent) => {
        e.preventDefault()
        setup.mutate(password, { onSuccess: (data) => { setPending(data); setPassword('') } })
      }}>
        <p className="text-sm">{t('mfa.intro')}</p>
        <PasswordField value={password} onChange={setPassword} />
        {setup.error && <p role="alert" className="text-sm text-danger">{errorMessage(setup.error, t)}</p>}
        <div className="flex gap-2">
          <button className="btn-primary" disabled={setup.isPending}>{t('mfa.continue')}</button>
          <button type="button" className="btn-secondary" onClick={onCancel}>{t('mfa.cancel')}</button>
        </div>
      </form>
    )
  }

  return (
    <form className="space-y-4" onSubmit={(e: FormEvent) => {
      e.preventDefault()
      confirm.mutate(code, { onSuccess: (data) => onCodes(data.recovery_codes) })
    }}>
      <p className="text-sm">{t('mfa.scanHelp')}</p>
      <img src={pending.qr_code} alt={t('mfa.qrAlt')} width={200} height={200}
        className="mx-auto rounded-2xl bg-white p-2" />
      <details className="text-sm">
        <summary className="cursor-pointer font-semibold text-primary">{t('mfa.manualKey')}</summary>
        <p className="mt-2 break-all font-mono tracking-wider" aria-label={t('mfa.manualKey')}>
          {pending.secret.match(/.{1,4}/g)?.join(' ')}
        </p>
      </details>
      <div>
        <label className="label" htmlFor="mfa-code">{t('mfa.code')}</label>
        <input id="mfa-code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]*" required maxLength={7}
          className="input font-mono tracking-widest" value={code} onChange={(e) => setCode(e.target.value)} />
      </div>
      {confirm.error && <p role="alert" className="text-sm text-danger">{errorMessage(confirm.error, t)}</p>}
      <div className="flex gap-2">
        <button className="btn-primary" disabled={confirm.isPending}>{t('mfa.confirm')}</button>
        <button type="button" className="btn-secondary" onClick={onCancel}>{t('mfa.cancel')}</button>
      </div>
    </form>
  )
}

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <h3 className="font-semibold">{t('mfa.recoveryTitle')}</h3>
        <p className="text-sm text-muted">{t('mfa.recoveryHelp')}</p>
      </div>
      <ul className="grid grid-cols-2 gap-2 rounded-2xl bg-surface-2 p-3 font-mono text-sm" aria-label={t('mfa.recoveryTitle')}>
        {codes.map((code) => <li key={code}>{code}</li>)}
      </ul>
      <div className="flex gap-2">
        <button type="button" className="btn-secondary" onClick={() => {
          void navigator.clipboard?.writeText(codes.join('\n')).then(() => setCopied(true))
        }}>
          {copied ? t('mfa.copied') : t('mfa.copy')}
        </button>
        <button type="button" className="btn-primary" onClick={onDone}>{t('mfa.done')}</button>
      </div>
    </div>
  )
}

function DisableForm({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation()
  const disable = useMfaDisable()
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [useRecovery, setUseRecovery] = useState(false)
  return (
    <form className="space-y-4" onSubmit={(e: FormEvent) => {
      e.preventDefault()
      disable.mutate(
        { password, ...(useRecovery ? { recovery_code: code } : { code }) },
        { onSuccess: onDone },
      )
    }}>
      <p className="text-sm">{t('mfa.disableHelp')}</p>
      <PasswordField value={password} onChange={setPassword} />
      <div>
        <label className="label" htmlFor="mfa-disable-code">{useRecovery ? t('mfa.recoveryCode') : t('mfa.code')}</label>
        <input id="mfa-disable-code" required maxLength={24} autoComplete={useRecovery ? 'off' : 'one-time-code'}
          inputMode={useRecovery ? 'text' : 'numeric'} className="input font-mono" value={code} onChange={(e) => setCode(e.target.value)} />
        <button type="button" className="mt-1.5 text-sm font-semibold text-primary hover:underline"
          onClick={() => { setUseRecovery(!useRecovery); setCode('') }}>
          {useRecovery ? t('mfa.useApp') : t('mfa.useRecovery')}
        </button>
      </div>
      {disable.error && <p role="alert" className="text-sm text-danger">{errorMessage(disable.error, t)}</p>}
      <div className="flex gap-2">
        <button className="btn-danger" disabled={disable.isPending}>{t('mfa.turnOff')}</button>
        <button type="button" className="btn-secondary" onClick={onDone}>{t('mfa.cancel')}</button>
      </div>
    </form>
  )
}

function NewCodesForm({ onCodes, onCancel }: { onCodes: (codes: string[]) => void; onCancel: () => void }) {
  const { t } = useTranslation()
  const renew = useNewRecoveryCodes()
  const [password, setPassword] = useState('')
  return (
    <form className="space-y-4" onSubmit={(e: FormEvent) => {
      e.preventDefault()
      renew.mutate(password, { onSuccess: (data) => onCodes(data.recovery_codes) })
    }}>
      <p className="text-sm">{t('mfa.newCodesHelp')}</p>
      <PasswordField value={password} onChange={setPassword} />
      {renew.error && <p role="alert" className="text-sm text-danger">{errorMessage(renew.error, t)}</p>}
      <div className="flex gap-2">
        <button className="btn-primary" disabled={renew.isPending}>{t('mfa.newCodes')}</button>
        <button type="button" className="btn-secondary" onClick={onCancel}>{t('mfa.cancel')}</button>
      </div>
    </form>
  )
}
