import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useReport } from '../api/social'
import { REPORT_REASONS, type ReportReason } from '../api/types'
import { errorMessage } from '../lib/errors'
import { Sheet } from '../ui/primitives'

export function ReportSheet({
  open,
  onClose,
  target,
}: {
  open: boolean
  onClose: () => void
  target: { type: 'listing' | 'user'; id: string }
}) {
  const { t } = useTranslation()
  const report = useReport()
  const [reason, setReason] = useState<ReportReason>('scam')
  const [details, setDetails] = useState('')

  function submit(e: FormEvent) {
    e.preventDefault()
    report.mutate({ target_type: target.type, target_id: target.id, reason, details: details.trim() || undefined })
  }
  function close() {
    report.reset()
    onClose()
  }
  return (
    <Sheet open={open} onClose={close} title={t(`social.report.title.${target.type}`)}>
      {report.isSuccess ? (
        <div className="space-y-4 py-2">
          <p>{t('social.report.thanks')}</p>
          <button type="button" className="btn-primary w-full" onClick={close}>
            {t('common.close')}
          </button>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <fieldset className="space-y-2">
            <legend className="label">{t('social.report.reason')}</legend>
            {REPORT_REASONS.map((r) => (
              <label
                key={r}
                className="flex min-h-11 cursor-pointer items-center gap-3 rounded-xl border border-line px-3 has-[:checked]:border-primary has-[:checked]:bg-primary-soft"
              >
                <input type="radio" name="reason" value={r} checked={reason === r} onChange={() => setReason(r)} />
                <span className="text-sm">{t(`social.report.reasons.${r}`)}</span>
              </label>
            ))}
          </fieldset>
          <div>
            <label className="label" htmlFor="report-details">
              {t('social.report.details')}
            </label>
            <textarea
              id="report-details"
              className="input min-h-24"
              maxLength={1000}
              value={details}
              onChange={(e) => setDetails(e.target.value)}
            />
          </div>
          {report.isError && (
            <p role="alert" className="text-sm text-danger">
              {errorMessage(report.error, t)}
            </p>
          )}
          <button className="btn-primary w-full" disabled={report.isPending}>
            {t('social.report.send')}
          </button>
        </form>
      )}
    </Sheet>
  )
}
