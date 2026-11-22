import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api, json } from '../api/client'
import { savedSearchLabel, searchPath, useDeleteSavedSearch, useSavedSearches } from '../api/discovery'
import { useFollowing, useSavedListings } from '../api/social'
import type { AdminReport, UserSummary } from '../api/types'
import { Avatar } from '../components/Avatar'
import { InfiniteGrid } from '../components/InfiniteGrid'
import { errorMessage } from '../lib/errors'
import { formatDateTime } from '../lib/format'
import { Icon } from '../ui/icons'
import { Badge, EmptyState, ErrorState, PageHeader, Spinner } from '../ui/primitives'

function SavedSearches() {
  const { t } = useTranslation()
  const searches = useSavedSearches()
  const remove = useDeleteSavedSearch()
  if (!searches.data?.length) return null
  return (
    <section className="mb-6" aria-labelledby="saved-searches">
      <h2 id="saved-searches" className="mb-1 font-semibold">{t('search.savedSearches')}</h2>
      <p className="mb-3 text-sm text-muted">{t('search.savedSearchesHint')}</p>
      <ul className="flex flex-wrap gap-2">
        {searches.data.map((s) => {
          const label = savedSearchLabel(s.params, t)
          return (
            <li key={s.id} className="flex items-center rounded-full border border-line bg-surface">
              <Link to={searchPath(s.params)} className="flex min-h-10 items-center gap-1.5 py-1.5 pr-1 pl-3.5 text-sm font-medium">
                <Icon name="bell" size={14} className="text-muted" />
                {label}
              </Link>
              <button
                type="button"
                className="grid h-10 w-10 place-items-center rounded-full text-muted hover:text-danger"
                aria-label={t('search.removeSaved', { name: label })}
                disabled={remove.isPending}
                onClick={() => remove.mutate(s.id)}
              >
                <Icon name="close" size={14} />
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

export function SavedPage() {
  const { t } = useTranslation()
  const saved = useSavedListings()
  return (
    <div>
      <PageHeader title={t('social.savedTitle')} back="/me" />
      <SavedSearches />
      <InfiniteGrid query={saved} empty={<EmptyState icon="heart" title={t('social.savedEmpty')} text={t('social.savedEmptyText')} action={{ label: t('feed.tabs.trending'), to: '/' }} />} />
    </div>
  )
}

function UserRow({ user, action }: { user: UserSummary; action?: React.ReactNode }) {
  const { t } = useTranslation()
  return (
    <li className="flex items-center gap-3 px-4 py-3">
      <Link to={`/users/${user.id}`} className="flex min-w-0 flex-1 items-center gap-3">
        <Avatar name={user.display_name} url={user.avatar_url} />
        <span className="min-w-0">
          <span className="block truncate font-semibold">{user.display_name}</span>
          <span className="block truncate text-xs text-muted">
            {user.city ? `${user.city} - ${user.state} · ` : ''}
            {t('social.followerCount', { count: user.follower_count })}
          </span>
        </span>
      </Link>
      {action}
    </li>
  )
}

export function FollowingPage() {
  const { t } = useTranslation()
  const following = useFollowing()
  const users = following.data?.pages.flatMap((p) => p.items) ?? []
  return (
    <div className="mx-auto max-w-xl">
      <PageHeader title={t('social.followingTitle')} back="/me" />
      {following.isPending && <div className="skeleton h-40" />}
      {following.isError && <ErrorState message={errorMessage(following.error, t)} onRetry={() => void following.refetch()} />}
      {following.isSuccess && users.length === 0 && (
        <EmptyState icon="users" title={t('feed.followingEmpty')} text={t('feed.followingText')} action={{ label: t('feed.tabs.trending'), to: '/' }} />
      )}
      {users.length > 0 && (
        <ul className="card divide-y divide-line">
          {users.map((u) => <UserRow key={u.id} user={u} />)}
        </ul>
      )}
      {following.hasNextPage && (
        <button className="btn-secondary mt-4 w-full" disabled={following.isFetchingNextPage} onClick={() => void following.fetchNextPage()}>
          {t('notifications.loadMore')}
        </button>
      )}
    </div>
  )
}

export function BlockedPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const blocked = useQuery({ queryKey: ['blocks'], queryFn: () => api<UserSummary[]>('/api/me/blocks') })
  const unblock = useMutation({
    mutationFn: (id: string) => api<void>(`/api/users/${id}/block`, { method: 'DELETE' }),
    onSuccess: () => void qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'me' }),
  })
  return (
    <div className="mx-auto max-w-xl">
      <PageHeader title={t('social.blockedTitle')} back="/me" />
      <p className="mb-4 text-sm text-muted">{t('social.blockedText')}</p>
      {blocked.isPending && <div className="skeleton h-24" />}
      {blocked.isError && <ErrorState message={errorMessage(blocked.error, t)} onRetry={() => void blocked.refetch()} />}
      {blocked.isSuccess && blocked.data.length === 0 && <EmptyState icon="ban" title={t('social.blockedEmpty')} />}
      {blocked.isSuccess && blocked.data.length > 0 && (
        <ul className="card divide-y divide-line">
          {blocked.data.map((u) => (
            <UserRow key={u.id} user={u} action={
              <button className="btn-secondary min-h-9 px-3 text-sm" disabled={unblock.isPending} onClick={() => unblock.mutate(u.id)}>
                {t('social.unblock')}
              </button>
            } />
          ))}
        </ul>
      )}
    </div>
  )
}

export function AdminReportsPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const reports = useQuery({ queryKey: ['admin-reports'], queryFn: () => api<AdminReport[]>('/api/admin/reports') })
  const resolve = useMutation({
    mutationFn: ({ id, outcome }: { id: string; outcome: 'dismissed' | 'actioned' }) =>
      api<{ closed: number }>(`/api/admin/reports/${id}/resolve`, { method: 'POST', body: json({ outcome }) }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['admin-reports'] }),
  })
  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader title={t('social.moderation.title')} back="/me" />
      {reports.isPending && <div className="flex justify-center py-10 text-primary"><Spinner /></div>}
      {reports.isError && <ErrorState message={errorMessage(reports.error, t)} onRetry={() => void reports.refetch()} />}
      {reports.isSuccess && reports.data.length === 0 && <EmptyState icon="shield" title={t('social.moderation.empty')} />}
      <ul className="space-y-3">
        {reports.data?.map((r) => (
          <li key={r.id} className="card space-y-3 p-4 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              {r.triage_priority && (
                <Badge tone={r.triage_priority === 1 ? 'danger' : r.triage_priority === 2 ? 'warning' : 'neutral'}>
                  {t(`review.priority.${r.triage_priority}`)}
                </Badge>
              )}
              <Badge tone="danger">{t(`social.report.reasons.${r.reason}`)}</Badge>
              <Badge>{t(`social.moderation.target.${r.target_type}`)}</Badge>
              {r.reports_on_target > 1 && <Badge tone="warning">{t('social.moderation.count', { count: r.reports_on_target })}</Badge>}
              <span className="ml-auto text-xs text-muted">{formatDateTime(r.created_at)}</span>
            </div>
            <Link to={r.target_type === 'listing' ? `/listings/${r.target_id}` : `/users/${r.target_id}`} className="flex items-center gap-1 font-semibold text-primary hover:underline">
              {r.target_label}<Icon name="chevronRight" size={16} />
            </Link>
            {r.details && <p className="rounded-xl bg-surface-2 p-3">{r.details}</p>}
            {r.triage_reason && (
              <p className="flex items-start gap-2 text-xs text-muted">
                <Icon name="sparkles" size={14} className="mt-0.5 shrink-0 text-primary" />
                <span>
                  <strong className="font-semibold text-fg">{t(`review.categories.${r.triage_category}`, { defaultValue: r.triage_category ?? '' })}</strong>
                  {' · '}{r.triage_reason}
                </span>
              </p>
            )}
            <div className="flex gap-2">
              <button className="btn-secondary flex-1" disabled={resolve.isPending} onClick={() => resolve.mutate({ id: r.id, outcome: 'dismissed' })}>
                {t('social.moderation.dismiss')}
              </button>
              <button className="btn-danger flex-1" disabled={resolve.isPending} onClick={() => resolve.mutate({ id: r.id, outcome: 'actioned' })}>
                {r.target_type === 'listing' ? t('social.moderation.remove') : t('social.moderation.actioned')}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

interface ReviewItem {
  id: string
  title: string
  description: string
  category_slug: string
  price_cents: number
  review_category: string | null
  review_reason: string | null
  updated_at: string
}

/** Listings held at publishing because the checks found they may be prohibited. */
export function AdminReviewPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const queue = useQuery({ queryKey: ['admin-review'], queryFn: () => api<ReviewItem[]>('/api/admin/review/listings') })
  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: 'approve' | 'reject' }) =>
      api<{ status: string }>(`/api/admin/review/listings/${id}`, { method: 'POST', body: json({ decision }) }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['admin-review'] }),
  })
  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader title={t('review.title')} back="/me" />
      <p className="mb-4 text-sm text-muted">{t('review.intro')}</p>
      {queue.isPending && <div className="flex justify-center py-10 text-primary"><Spinner /></div>}
      {queue.isError && <ErrorState message={errorMessage(queue.error, t)} onRetry={() => void queue.refetch()} />}
      {queue.isSuccess && queue.data.length === 0 && <EmptyState icon="shield" title={t('review.empty')} />}
      <ul className="space-y-3">
        {queue.data?.map((item) => (
          <li key={item.id} className="card space-y-3 p-4 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              {item.review_category && <Badge tone="danger">{t(`review.categories.${item.review_category}`, { defaultValue: item.review_category })}</Badge>}
              <span className="ml-auto text-xs text-muted">{formatDateTime(item.updated_at)}</span>
            </div>
            <Link to={`/listings/${item.id}`} className="flex items-center gap-1 font-semibold text-primary hover:underline">
              {item.title}<Icon name="chevronRight" size={16} />
            </Link>
            <p className="line-clamp-3 rounded-xl bg-surface-2 p-3">{item.description}</p>
            {item.review_reason && (
              <p className="flex items-start gap-2 text-xs text-muted">
                <Icon name="sparkles" size={14} className="mt-0.5 shrink-0 text-primary" />
                {item.review_reason}
              </p>
            )}
            <div className="flex gap-2">
              <button className="btn-secondary flex-1" disabled={decide.isPending} onClick={() => decide.mutate({ id: item.id, decision: 'approve' })}>
                {t('review.approve')}
              </button>
              <button className="btn-danger flex-1" disabled={decide.isPending} onClick={() => decide.mutate({ id: item.id, decision: 'reject' })}>
                {t('review.reject')}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
