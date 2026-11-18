import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { NOTIFICATIONS_KEY, fetchNotifications, markAllRead, notificationTarget } from '../api/notifications'
import { NotificationText } from '../components/NotificationText'
import { errorMessage } from '../lib/errors'
import { formatDateTime } from '../lib/format'
import { EmptyState, ErrorState, PageHeader } from '../ui/primitives'

export function NotificationsPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const list = useQuery({ queryKey: NOTIFICATIONS_KEY, queryFn: fetchNotifications })
  const readAll = useMutation({
    mutationFn: markAllRead,
    onSuccess: () => qc.invalidateQueries({ queryKey: NOTIFICATIONS_KEY }),
  })

  return (
    <div className="mx-auto max-w-xl">
      <PageHeader
        title={t('notifications.title')}
        action={(list.data?.unread ?? 0) > 0 && (
          <button className="btn-ghost min-h-9 text-sm text-primary" onClick={() => readAll.mutate()}>{t('notifications.markAllRead')}</button>
        )}
      />
      {list.isPending && <div className="space-y-2">{[1, 2, 3].map((i) => <div key={i} className="skeleton h-16" />)}</div>}
      {list.isError && <ErrorState message={errorMessage(list.error, t)} onRetry={() => void list.refetch()} />}
      {list.data?.items.length === 0 && <EmptyState icon="bell" title={t('notifications.empty')} />}
      {list.data && list.data.items.length > 0 && (
        <ul className="card divide-y divide-line overflow-hidden">
          {list.data.items.map((n) => (
            <li key={n.id}>
              <Link to={notificationTarget(n)} className={`flex gap-3 px-4 py-3 hover:bg-surface-2 ${n.read_at ? '' : 'bg-primary-soft/50'}`}>
                {!n.read_at && <span className="mt-2 h-2 w-2 shrink-0 rounded-full bg-primary" aria-hidden />}
                <span className="text-sm">
                  <NotificationText n={n} />
                  <span className="mt-1 block text-xs text-muted">{formatDateTime(n.created_at)}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
