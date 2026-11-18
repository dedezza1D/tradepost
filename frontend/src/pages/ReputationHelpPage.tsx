import { useTranslation } from 'react-i18next'
import { ReputationBadge } from '../components/Reputation'
import { PageHeader } from '../ui/primitives'

/** Public explanation of how seller reputation is computed (mirrors the backend rules). */
export function ReputationHelpPage() {
  const { t } = useTranslation()
  const levels = ['top', 'good', 'new', 'attention'] as const
  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <PageHeader title={t('reputation.helpTitle')} />
      <p className="text-[15px] leading-relaxed">{t('reputation.helpIntro')}</p>
      <section className="card space-y-2 p-5">
        <h2 className="font-semibold">{t('reputation.inputsTitle')}</h2>
        <ul className="list-disc space-y-1 pl-5 text-sm">
          <li>{t('reputation.inputs.sales')}</li>
          <li>{t('reputation.inputs.cancellations')}</li>
          <li>{t('reputation.inputs.late')}</li>
          <li>{t('reputation.inputs.ratings')}</li>
        </ul>
      </section>
      <ul className="space-y-3">
        {levels.map((level) => (
          <li key={level} className="card flex flex-col gap-2 p-4 sm:flex-row sm:items-center">
            <span className="sm:w-40"><ReputationBadge level={level} /></span>
            <p className="flex-1 text-sm">{t(`reputation.rules.${level}`)}</p>
          </li>
        ))}
      </ul>
      <p className="text-xs text-muted">{t('reputation.helpFooter')}</p>
    </div>
  )
}
