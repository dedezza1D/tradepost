import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { useMe } from '../api/auth'
import {
  type Category,
  type CategoryPreference,
  currentSubscription,
  disablePush,
  enablePush,
  pushSupport,
  sendTestPush,
  useNotificationPreferences,
  useSetPreference,
} from '../api/push'
import { errorMessage } from '../lib/errors'
import { Icon } from '../ui/icons'

type DeviceState = 'checking' | 'on' | 'off'

function DevicePush({ publicKey }: { publicKey: string }) {
  const { t } = useTranslation()
  const { data: me } = useMe()
  const qc = useQueryClient()
  const [state, setState] = useState<DeviceState>('checking')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ text: string; error?: boolean } | null>(null)
  const support = pushSupport()

  useEffect(() => {
    let live = true
    void currentSubscription()
      .then((s) => live && setState(s ? 'on' : 'off'))
      .catch(() => live && setState('off'))
    return () => { live = false }
  }, [])

  if (support === 'needs-install') return <p className="text-sm text-muted">{t('push.needsInstall')}</p>
  if (support === 'unsupported') return <p className="text-sm text-muted">{t('push.unsupported')}</p>
  // Blocked earlier in the browser: only its settings can undo that, a button here cannot.
  if (state === 'off' && Notification.permission === 'denied') {
    return <p className="text-sm text-muted">{t('push.denied')}</p>
  }

  const run = async (action: () => Promise<void>) => {
    setBusy(true)
    setNote(null)
    try {
      await action()
    } catch (error) {
      setNote({ text: errorMessage(error, t), error: true })
    } finally {
      setBusy(false)
      void qc.invalidateQueries({ queryKey: ['notification-preferences'] })
    }
  }
  const enable = () => run(async () => {
    if (!me) return
    const result = await enablePush(publicKey, me.id)
    if (result === 'enabled') setState('on')
    else setNote({ text: t(result === 'denied' ? 'push.denied' : 'push.noWorker'), error: true })
  })
  const disable = () => run(async () => { await disablePush(); setState('off') })
  const test = () => run(async () => {
    const result = await sendTestPush()
    setNote({ text: result.sent ? t('push.testSent') : t('push.testNone') })
  })

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-surface-2 p-3">
        <div className="flex items-center gap-2 text-sm">
          <Icon name="bell" size={18} />
          <span>{t('push.thisDevice')}: <strong>{state === 'checking' ? '…' : t(state === 'on' ? 'push.on' : 'push.off')}</strong></span>
        </div>
        <div className="flex gap-2">
          {state === 'on' && (
            <button type="button" className="btn-ghost min-h-9 px-3 text-sm" onClick={() => void test()} disabled={busy}>{t('push.test')}</button>
          )}
          <button type="button" className={`${state === 'on' ? 'btn-secondary' : 'btn-primary'} min-h-9 px-4 text-sm`}
            onClick={() => void (state === 'on' ? disable() : enable())} disabled={busy || state === 'checking'}>
            {t(state === 'on' ? 'push.disable' : 'push.enable')}
          </button>
        </div>
      </div>
      {note && <p role={note.error ? 'alert' : 'status'} className={`text-sm ${note.error ? 'text-danger' : 'text-muted'}`}>{note.text}</p>}
    </div>
  )
}

export function NotificationSettings() {
  const { t } = useTranslation()
  const prefs = useNotificationPreferences()
  const setPreference = useSetPreference()
  const location = useLocation()
  const section = useRef<HTMLElement>(null)
  const data = prefs.data

  // Boxes change as soon as they are clicked; the saved value takes over when the server answers.
  const [pending, setPending] = useState<Record<string, boolean>>({})
  const scrolled = useRef(false)
  const loaded = Boolean(data)

  // E-mails link here ("choose which notices you get"): scroll once, when the list is in.
  useEffect(() => {
    if (location.hash === '#notifications' && loaded && !scrolled.current) {
      scrolled.current = true
      section.current?.scrollIntoView({ block: 'start' })
    }
  }, [location.hash, loaded])

  const shown = (c: CategoryPreference, channel: 'push' | 'email') => pending[`${c.category}:${channel}`] ?? c[channel]

  const toggle = (category: Category, channel: 'push' | 'email', value: boolean) => {
    const current = data?.categories.find((c) => c.category === category)
    if (!current) return
    const key = `${category}:${channel}`
    setPending((p) => ({ ...p, [key]: value }))
    setPreference.mutate(
      { category, push: shown(current, 'push'), email: shown(current, 'email'), [channel]: value },
      {
        onSettled: () => setPending((p) => {
          const rest = { ...p }
          delete rest[key]
          return rest
        }),
      },
    )
  }

  return (
    <section id="notifications" ref={section} className="card scroll-mt-40 space-y-4 p-5" aria-labelledby="notifications-title">
      <div>
        <h2 id="notifications-title" className="font-semibold">{t('push.title')}</h2>
        <p className="text-sm text-muted">{t('push.hint')}</p>
      </div>
      {prefs.isPending && <div className="skeleton h-40" />}
      {data && (
        <>
          {data.push_available && data.public_key
            ? <DevicePush publicKey={data.public_key} />
            : <p className="text-sm text-muted">{t('push.serverOff')}</p>}
          <table className="w-full text-sm">
            <caption className="sr-only">{t('push.tableCaption')}</caption>
            <thead className="text-xs text-muted">
              <tr>
                <th scope="col" className="py-1 text-left font-medium">{t('push.kind')}</th>
                <th scope="col" className="w-16 py-1 text-center font-medium">{t('push.push')}</th>
                <th scope="col" className="w-16 py-1 text-center font-medium">{t('push.email')}</th>
              </tr>
            </thead>
            <tbody>
              {data.categories.map((c) => (
                <tr key={c.category} className="border-t border-line align-top">
                  <th scope="row" className="py-2.5 pr-2 text-left font-normal">
                    <span className="block font-medium">{t(`push.category.${c.category}`)}</span>
                    <span className="block text-xs text-muted">{t(`push.categoryHint.${c.category}`)}</span>
                  </th>
                  <td className="py-2.5 text-center">
                    <input type="checkbox" className="h-5 w-5 accent-primary" checked={shown(c, 'push')}
                      aria-label={t('push.pushFor', { category: t(`push.category.${c.category}`) })}
                      onChange={(e) => toggle(c.category, 'push', e.target.checked)} />
                  </td>
                  <td className="py-2.5 text-center">
                    {c.email_possible ? (
                      <input type="checkbox" className="h-5 w-5 accent-primary" checked={shown(c, 'email')}
                        aria-label={t('push.emailFor', { category: t(`push.category.${c.category}`) })}
                        onChange={(e) => toggle(c.category, 'email', e.target.checked)} />
                    ) : (
                      <><span className="text-muted" aria-hidden>—</span><span className="sr-only">{t('push.noEmail')}</span></>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-xs text-muted">{data.email_available ? t('push.emailNote') : t('push.emailOff')}</p>
          {setPreference.isError && <p role="alert" className="text-sm text-danger">{errorMessage(setPreference.error, t)}</p>}
        </>
      )}
    </section>
  )
}
