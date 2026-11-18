import { useTranslation } from 'react-i18next'
import { useLocation, useNavigate } from 'react-router-dom'
import { useMe } from '../api/auth'
import { useToggleSave } from '../api/social'
import { Icon } from '../ui/icons'

/** Heart toggle. Visitors who are not signed in are sent to sign in first. */
export function SaveButton({
  id,
  saved,
  title,
  variant = 'overlay',
}: {
  id: string
  saved: boolean
  title: string
  variant?: 'overlay' | 'inline'
}) {
  const { t } = useTranslation()
  const { data: me } = useMe()
  const navigate = useNavigate()
  const location = useLocation()
  const toggle = useToggleSave()

  function onClick() {
    if (!me) {
      navigate('/login', { state: { from: location.pathname + location.search } })
      return
    }
    toggle.mutate({ id, save: !saved })
  }
  const label = saved ? t('social.unsave', { title }) : t('social.save', { title })
  const heart = <Icon name="heart" size={variant === 'overlay' ? 20 : 18} fill={saved ? 'currentColor' : 'none'} />
  if (variant === 'inline') {
    return (
      <button
        type="button"
        className={`btn-ghost min-h-10 rounded-full px-3 ${saved ? 'text-danger' : ''}`}
        aria-pressed={saved}
        aria-label={label}
        onClick={onClick}
        disabled={toggle.isPending}
      >
        {heart}
        {saved ? t('social.saved') : t('social.saveShort')}
      </button>
    )
  }
  return (
    <button
      type="button"
      aria-pressed={saved}
      aria-label={label}
      onClick={onClick}
      disabled={toggle.isPending}
      className={`grid h-10 w-10 place-items-center rounded-full bg-surface/90 shadow-sm backdrop-blur transition hover:scale-105 ${saved ? 'text-danger' : 'text-fg'}`}
    >
      {heart}
    </button>
  )
}
