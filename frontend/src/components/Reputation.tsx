import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { Icon } from '../ui/icons'

export type ReputationLevel = 'new' | 'top' | 'good' | 'attention'

const TONES: Record<ReputationLevel, string> = {
  top: 'bg-success-soft text-success',
  good: 'bg-primary-soft text-primary',
  new: 'bg-surface-2 text-muted',
  attention: 'bg-warning-soft text-warning-fg',
}

export function ReputationBadge({ level }: { level: string }) {
  const { t } = useTranslation()
  const known = (level in TONES ? level : 'new') as ReputationLevel
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold ${TONES[known]}`}>
      <Icon name={known === 'attention' ? 'info' : 'shield'} size={13} />
      {t(`reputation.level.${known}`)}
    </span>
  )
}

export interface ReputationData {
  level: string
  completed_sales: number
  cancel_rate: number
  late_rate: number
  rating_avg: number | null
  rating_count: number
}

const pct = (v: number) => `${Math.round(v * 100)}%`

export function ReputationCard({ data }: { data: ReputationData }) {
  const { t } = useTranslation()
  return (
    <section className="card space-y-3 p-5" aria-labelledby="reputation-title">
      <div className="flex items-center justify-between gap-2">
        <h2 id="reputation-title" className="font-semibold">{t('reputation.title')}</h2>
        <ReputationBadge level={data.level} />
      </div>
      <dl className="grid grid-cols-3 gap-2 text-center">
        <div className="rounded-xl bg-surface-2 p-2"><dt className="text-xs text-muted">{t('reputation.sales')}</dt><dd className="font-bold">{data.completed_sales}</dd></div>
        <div className="rounded-xl bg-surface-2 p-2"><dt className="text-xs text-muted">{t('reputation.cancellations')}</dt><dd className="font-bold">{pct(data.cancel_rate)}</dd></div>
        <div className="rounded-xl bg-surface-2 p-2"><dt className="text-xs text-muted">{t('reputation.late')}</dt><dd className="font-bold">{pct(data.late_rate)}</dd></div>
      </dl>
      <p className="text-xs text-muted">
        {t('reputation.window')}{' '}
        <Link to="/help/reputation" className="font-semibold text-primary hover:underline">{t('reputation.how')}</Link>
      </p>
    </section>
  )
}
