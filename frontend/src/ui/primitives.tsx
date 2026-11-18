import { useEffect, useRef, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { Icon, type IconName } from './icons'

export function Spinner({ className = '' }: { className?: string }) {
  return (
    <span
      role="status"
      className={`inline-block h-5 w-5 animate-spin rounded-full border-2 border-current border-r-transparent ${className}`}
    />
  )
}

export function Badge({
  children,
  tone = 'neutral',
}: {
  children: ReactNode
  tone?: 'neutral' | 'primary' | 'success' | 'warning' | 'danger'
}) {
  const tones = {
    neutral: 'bg-surface-2 text-muted',
    primary: 'bg-primary-soft text-primary',
    success: 'bg-success-soft text-success',
    warning: 'bg-warning-soft text-warning-fg',
    danger: 'bg-danger-soft text-danger',
  }
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${tones[tone]}`}>
      {children}
    </span>
  )
}

export function EmptyState({
  icon,
  title,
  text,
  action,
}: {
  icon: IconName
  title: string
  text?: string
  action?: { label: string; to: string }
}) {
  return (
    <div className="card flex flex-col items-center gap-3 px-6 py-12 text-center">
      <span className="grid h-14 w-14 place-items-center rounded-full bg-primary-soft text-primary">
        <Icon name={icon} size={26} />
      </span>
      <p className="text-base font-semibold">{title}</p>
      {text && <p className="max-w-sm text-sm text-muted">{text}</p>}
      {action && (
        <Link to={action.to} className="btn-primary mt-2">
          {action.label}
        </Link>
      )}
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  const { t } = useTranslation()
  return (
    <div role="alert" className="card flex flex-col items-center gap-3 px-6 py-10 text-center">
      <span className="grid h-12 w-12 place-items-center rounded-full bg-danger-soft text-danger">
        <Icon name="info" />
      </span>
      <p className="text-sm">{message}</p>
      {onRetry && (
        <button className="btn-secondary" onClick={onRetry}>
          {t('common.retry')}
        </button>
      )}
    </div>
  )
}

export function PageHeader({ title, back, action }: { title: string; back?: string; action?: ReactNode }) {
  const { t } = useTranslation()
  return (
    <div className="mb-4 flex items-center gap-2">
      {back && (
        <Link to={back} className="btn-ghost -ml-2 min-h-10 px-2" aria-label={t('common.back')}>
          <Icon name="chevronLeft" />
        </Link>
      )}
      <h1 className="flex-1 text-xl font-bold tracking-tight sm:text-2xl">{title}</h1>
      {action}
    </div>
  )
}

/**
 * Bottom sheet on phones, centered dialog on larger screens. Native <dialog> gives focus
 * trapping, Esc to close and an inert background for free.
 */
export function Sheet({
  open,
  onClose,
  title,
  children,
  footer,
}: {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
  footer?: ReactNode
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const { t } = useTranslation()
  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
  }, [open])
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
      className="m-0 mt-auto w-full max-w-none rounded-t-3xl bg-surface p-0 text-fg backdrop:bg-black/40 sm:m-auto sm:max-w-lg sm:rounded-3xl"
    >
      <div className="flex max-h-[85dvh] flex-col">
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button className="btn-ghost min-h-9 px-2" onClick={onClose} aria-label={t('common.close')}>
            <Icon name="close" />
          </button>
        </div>
        <div className="overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="safe-bottom border-t border-line px-5 py-3">{footer}</div>}
      </div>
    </dialog>
  )
}

export function Stars({ value, size = 16 }: { value: number; size?: number }) {
  const { t } = useTranslation()
  const rounded = Math.round(value)
  return (
    <span className="inline-flex text-accent" role="img" aria-label={t('reviews.starsLabel', { value: value.toFixed(1) })}>
      {[1, 2, 3, 4, 5].map((n) => (
        <Icon key={n} name="star" size={size} fill={n <= rounded ? 'currentColor' : 'none'} className={n <= rounded ? '' : 'opacity-40'} />
      ))}
    </span>
  )
}
