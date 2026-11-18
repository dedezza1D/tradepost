import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { useMe } from '../api/auth'
import { ApiError, api } from '../api/client'
import { useToggleBlock, useToggleFollow, useUserListings } from '../api/social'
import type { Profile } from '../api/types'
import { Avatar } from '../components/Avatar'
import { InfiniteGrid } from '../components/InfiniteGrid'
import { ReportSheet } from '../components/ReportSheet'
import { errorMessage } from '../lib/errors'
import { formatDateTime } from '../lib/format'
import { Icon } from '../ui/icons'
import { EmptyState, ErrorState, Sheet, Stars } from '../ui/primitives'

interface SellerReviews {
  reviews: { id: string; rating: number; comment: string | null; buyer_name: string; items: string[]; created_at: string }[]
}

function Reviews({ userId }: { userId: string }) {
  const { t } = useTranslation()
  const data = useQuery({ queryKey: ['seller-reviews', userId], queryFn: () => api<SellerReviews>(`/api/users/${userId}/reviews`) })
  if (data.isPending) return <div className="skeleton h-24" />
  if (data.isError) return <ErrorState message={errorMessage(data.error, t)} onRetry={() => void data.refetch()} />
  if (data.data.reviews.length === 0) return <EmptyState icon="star" title={t('reviews.none')} />
  return (
    <ul className="space-y-3">
      {data.data.reviews.map((r) => (
        <li key={r.id} className="card p-4 text-sm">
          <div className="flex items-center justify-between gap-2">
            <Stars value={r.rating} size={15} />
            <span className="text-xs text-muted">{formatDateTime(r.created_at)}</span>
          </div>
          {r.comment && <p className="mt-2 leading-relaxed">{r.comment}</p>}
          <p className="mt-2 text-xs text-muted">{t('reviews.bought', { name: r.buyer_name, items: r.items.join(', ') })}</p>
        </li>
      ))}
    </ul>
  )
}

function Stat({ value, label }: { value: number; label: string }) {
  return (
    <div className="text-center">
      <p className="text-lg font-bold">{value}</p>
      <p className="text-xs text-muted">{label}</p>
    </div>
  )
}

function Actions({ profile }: { profile: Profile }) {
  const { t } = useTranslation()
  const { data: me } = useMe()
  const navigate = useNavigate()
  const location = useLocation()
  const follow = useToggleFollow(profile.id)
  const block = useToggleBlock(profile.id)
  const [menu, setMenu] = useState(false)
  const [reporting, setReporting] = useState(false)

  if (profile.is_me) {
    return <Link to="/me" className="btn-secondary w-full sm:w-auto">{t('social.editProfile')}</Link>
  }
  const signIn = () => navigate('/login', { state: { from: location.pathname } })

  async function share() {
    const url = window.location.href
    if (navigator.share) {
      try {
        await navigator.share({ title: profile.display_name, url })
      } catch {
        // Cancelled by the user.
      }
    } else {
      await navigator.clipboard.writeText(url)
    }
    setMenu(false)
  }

  return (
    <div className="flex gap-2">
      {!profile.blocked && (
        <button
          type="button"
          className={`${profile.following ? 'btn-secondary' : 'btn-primary'} flex-1 sm:flex-none`}
          aria-pressed={profile.following}
          disabled={follow.isPending}
          onClick={() => (me ? follow.mutate(!profile.following) : signIn())}
        >
          {profile.following ? <><Icon name="check" size={18} />{t('social.following')}</> : <><Icon name="plus" size={18} />{t('social.follow')}</>}
        </button>
      )}
      <button type="button" className="btn-secondary px-3" aria-label={t('social.moreActions')} onClick={() => setMenu(true)}>
        <Icon name="more" />
      </button>
      {follow.isError && <p role="alert" className="text-sm text-danger">{errorMessage(follow.error, t)}</p>}

      <Sheet open={menu} onClose={() => setMenu(false)} title={profile.display_name}>
        <ul className="-mx-2 space-y-1">
          <li>
            <button type="button" className="btn-ghost w-full justify-start" onClick={() => void share()}>
              <Icon name="share" size={18} />{t('social.shareProfile')}
            </button>
          </li>
          <li>
            <button
              type="button"
              className="btn-ghost w-full justify-start"
              disabled={block.isPending}
              onClick={() => {
                if (!me) return signIn()
                if (profile.blocked || window.confirm(t('social.blockConfirm', { name: profile.display_name }))) {
                  block.mutate(!profile.blocked, { onSuccess: () => setMenu(false) })
                }
              }}
            >
              <Icon name="ban" size={18} />
              {profile.blocked ? t('social.unblock') : t('social.block')}
            </button>
          </li>
          <li>
            <button type="button" className="btn-ghost w-full justify-start text-danger"
              onClick={() => { setMenu(false); if (me) setReporting(true); else signIn() }}>
              <Icon name="flag" size={18} />{t('social.report.user')}
            </button>
          </li>
        </ul>
      </Sheet>
      <ReportSheet open={reporting} onClose={() => setReporting(false)} target={{ type: 'user', id: profile.id }} />
    </div>
  )
}

export function SellerProfilePage() {
  const { t } = useTranslation()
  const { id = '' } = useParams()
  const [tab, setTab] = useState<'listings' | 'reviews'>('listings')
  const profile = useQuery({
    queryKey: ['profile', id],
    queryFn: () => api<Profile>(`/api/users/${id}`),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
  })
  const listings = useUserListings(id, profile.isSuccess && !profile.data.blocked)

  if (profile.isPending) return <div className="mx-auto max-w-4xl space-y-3"><div className="skeleton h-40" /><div className="skeleton h-64" /></div>
  if (profile.isError) {
    return profile.error instanceof ApiError && profile.error.status === 404 ? (
      <EmptyState icon="user" title={t('social.profileNotFound')} action={{ label: t('listing.backToListings'), to: '/' }} />
    ) : (
      <ErrorState message={errorMessage(profile.error, t)} onRetry={() => void profile.refetch()} />
    )
  }
  const p = profile.data
  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <header className="card space-y-4 p-5">
        <div className="flex items-start gap-4">
          <Avatar name={p.display_name} url={p.avatar_url} size="xl" />
          <div className="min-w-0 flex-1 space-y-1">
            <h1 className="truncate text-xl font-bold sm:text-2xl">{p.display_name}</h1>
            {p.city && <p className="flex items-center gap-1 text-sm text-muted"><Icon name="pin" size={15} />{p.city} - {p.state}</p>}
            <p className="flex items-center gap-1.5 text-sm">
              {p.rating_avg !== null ? (
                <><Stars value={p.rating_avg} /> <strong>{p.rating_avg.toFixed(1)}</strong> · {t('reviews.count', { count: p.rating_count })}</>
              ) : <span className="text-muted">{t('reviews.none')}</span>}
            </p>
            <p className="text-xs text-muted">{t('seller.memberSince', { date: new Date(p.member_since).toLocaleDateString() })}</p>
          </div>
        </div>
        {p.bio && <p className="text-[15px] leading-relaxed whitespace-pre-line">{p.bio}</p>}
        <div className="grid grid-cols-3 rounded-2xl bg-surface-2 py-3">
          <Stat value={p.listing_count} label={t('social.listings', { count: p.listing_count })} />
          <Stat value={p.follower_count} label={t('social.followers', { count: p.follower_count })} />
          <Stat value={p.following_count} label={t('social.followingCount', { count: p.following_count })} />
        </div>
        <Actions profile={p} />
      </header>

      {p.blocked ? (
        <EmptyState icon="ban" title={t('social.youBlocked', { name: p.display_name })} text={t('social.youBlockedText')} />
      ) : (
        <>
          <div role="tablist" aria-label={p.display_name} className="flex gap-1 border-b border-line">
            {(['listings', 'reviews'] as const).map((x) => (
              <button key={x} type="button" role="tab" aria-selected={tab === x} onClick={() => setTab(x)}
                className={`-mb-px min-h-11 border-b-2 px-3 text-sm font-semibold ${tab === x ? 'border-primary text-primary' : 'border-transparent text-muted hover:text-fg'}`}>
                {x === 'listings' ? t('social.tabListings') : t('reviews.title')}
              </button>
            ))}
          </div>
          <div role="tabpanel">
            {tab === 'listings' ? (
              <InfiniteGrid query={listings} empty={<EmptyState icon="tag" title={t('social.noListings')} />} />
            ) : (
              <Reviews userId={p.id} />
            )}
          </div>
        </>
      )}
    </div>
  )
}
