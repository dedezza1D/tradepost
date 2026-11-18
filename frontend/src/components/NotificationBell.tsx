import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import {
  NOTIFICATIONS_KEY,
  fetchNotifications,
  markAllRead,
  useNotificationStream,
} from '../api/notifications'
import { notificationTarget } from '../api/notifications'
import { NotificationText } from './NotificationText'
import { formatDateTime } from '../lib/format'
import { Icon } from '../ui/icons'

/** Header bell: live unread badge, quick panel on desktop, full page on phones. */
export function NotificationBell() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const panel = useRef<HTMLDivElement>(null)
  useNotificationStream(true)
  const { data } = useQuery({ queryKey: NOTIFICATIONS_KEY, queryFn: fetchNotifications })
  const readAll = useMutation({
    mutationFn: markAllRead,
    onSuccess: () => qc.invalidateQueries({ queryKey: NOTIFICATIONS_KEY }),
  })
  const unread = data?.unread ?? 0
  const label = `${t('notifications.open')}${unread ? ` (${t('notifications.unread', { count: unread })})` : ''}`

  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (panel.current && !panel.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const badge = unread > 0 && (
    <span className="absolute -top-0.5 -right-0.5 min-w-5 rounded-full bg-danger px-1 text-center text-[11px] leading-5 font-bold text-white">
      {unread > 99 ? '99+' : unread}
    </span>
  )

  return (
    <div className="relative" ref={panel}>
      <Link to="/notifications" className="relative block rounded-full p-2 text-fg hover:bg-surface-2 md:hidden" aria-label={label}>
        <Icon name="bell" />
        {badge}
      </Link>
      <button type="button" className="relative hidden rounded-full p-2 text-fg hover:bg-surface-2 md:block"
        aria-label={label} aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <Icon name="bell" />
        {badge}
      </button>
      {open && (
        <div role="dialog" aria-label={t('notifications.title')} className="card absolute right-0 z-30 mt-2 w-96 overflow-hidden shadow-lg">
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <h2 className="font-semibold">{t('notifications.title')}</h2>
            {unread > 0 && (
              <button className="text-xs font-semibold text-primary hover:underline" onClick={() => readAll.mutate()}>
                {t('notifications.markAllRead')}
              </button>
            )}
          </div>
          <ul className="max-h-96 divide-y divide-line overflow-y-auto">
            {data?.items.length === 0 && <li className="px-4 py-8 text-center text-sm text-muted">{t('notifications.empty')}</li>}
            {data?.items.slice(0, 8).map((n) => (
              <li key={n.id}>
                <Link to={notificationTarget(n)} onClick={() => setOpen(false)}
                  className={`block px-4 py-3 text-sm hover:bg-surface-2 ${n.read_at ? 'text-muted' : 'bg-primary-soft/50'}`}>
                  <NotificationText n={n} />
                  <span className="mt-1 block text-xs text-muted">{formatDateTime(n.created_at)}</span>
                </Link>
              </li>
            ))}
          </ul>
          <Link to="/notifications" onClick={() => setOpen(false)} className="block border-t border-line px-4 py-3 text-center text-sm font-semibold text-primary hover:bg-surface-2">
            {t('notifications.seeAll')}
          </Link>
        </div>
      )}
    </div>
  )
}
