import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { api, json } from '../api/client'
import { errorMessage } from '../lib/errors'
import { formatDateTime, formatMoney, parseMoney } from '../lib/format'
import { Badge, EmptyState, ErrorState, PageHeader, Spinner } from '../ui/primitives'

export interface Dispute {
  id: string
  order_id: string
  reason: 'not_received' | 'not_as_described' | 'damaged' | 'other'
  status: 'open' | 'seller_rejected' | 'escalated' | 'refunded' | 'rejected' | 'cancelled'
  refund_cents: number | null
  resolution_note: string | null
  resolved_by: string | null
  created_at: string
  resolved_at: string | null
  order_total_cents: number
  my_role: 'buyer' | 'seller' | 'platform'
  can_escalate: boolean
  messages: { id: number; author_role: string; body: string; created_at: string }[]
}

const TONES = {
  open: 'warning',
  seller_rejected: 'warning',
  escalated: 'danger',
  refunded: 'success',
  rejected: 'neutral',
  cancelled: 'neutral',
} as const

export function DisputeBadge({ status }: { status: Dispute['status'] }) {
  const { t } = useTranslation()
  return <Badge tone={TONES[status]}>{t(`disputes.status.${status}`)}</Badge>
}

function RefundForm({ dispute, onDone }: { dispute: Dispute; onDone: () => void }) {
  const { t } = useTranslation()
  const [partial, setPartial] = useState(false)
  const [amount, setAmount] = useState('')
  const [note, setNote] = useState('')
  const cents = parseMoney(amount)
  const refund = useMutation({
    mutationFn: () =>
      api<Dispute>(`/api/disputes/${dispute.id}/refund`, {
        method: 'POST',
        body: json({ amount_cents: partial ? cents : null, note: note.trim() || null }),
      }),
    onSuccess: onDone,
  })
  const invalid = partial && (cents === null || cents <= 0 || cents > dispute.order_total_cents)
  return (
    <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); refund.mutate() }}>
      <div className="flex gap-2" role="radiogroup" aria-label={t('disputes.refundTitle')}>
        {[false, true].map((p) => (
          <label key={String(p)} className="flex flex-1 cursor-pointer items-center gap-2 rounded-xl border border-line p-3 text-sm has-checked:border-primary has-checked:bg-primary-soft">
            <input type="radio" name="refund-kind" checked={partial === p} onChange={() => setPartial(p)} />
            {p ? t('disputes.partial') : t('disputes.full', { amount: formatMoney(dispute.order_total_cents) })}
          </label>
        ))}
      </div>
      {partial && (
        <div>
          <label className="label" htmlFor="refund-amount">{t('disputes.amount')}</label>
          <input id="refund-amount" className="input" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0,00" />
        </div>
      )}
      <div>
        <label className="label" htmlFor="refund-note">{t('disputes.note')}</label>
        <input id="refund-note" className="input" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
      </div>
      {refund.isError && <p role="alert" className="text-sm text-danger">{errorMessage(refund.error, t)}</p>}
      <button className="btn-primary w-full" disabled={invalid || refund.isPending}>{t('disputes.refund')}</button>
    </form>
  )
}

export function DisputePage() {
  const { t } = useTranslation()
  const { id = '' } = useParams()
  const qc = useQueryClient()
  const key = ['dispute', id]
  const dispute = useQuery({ queryKey: key, queryFn: () => api<Dispute>(`/api/disputes/${id}`) })
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: key })
    void qc.invalidateQueries({ queryKey: ['orders'] })
  }
  const [message, setMessage] = useState('')
  const [rejectNote, setRejectNote] = useState('')
  const post = (path: string, body?: unknown) => api<Dispute>(`/api/disputes/${id}/${path}`, { method: 'POST', body: body ? json(body) : undefined })
  const send = useMutation({ mutationFn: () => post('messages', { body: message.trim() }), onSuccess: () => { setMessage(''); refresh() } })
  const act = useMutation({ mutationFn: ({ path, body }: { path: string; body?: unknown }) => post(path, body), onSuccess: refresh })

  if (dispute.isPending) return <div className="flex justify-center py-16 text-primary"><Spinner /></div>
  if (dispute.isError) return <ErrorState message={errorMessage(dispute.error, t)} onRetry={() => void dispute.refetch()} />
  const d = dispute.data
  const active = d.status === 'open' || d.status === 'seller_rejected' || d.status === 'escalated'
  const sellerDecides = d.my_role === 'seller' && (d.status === 'open' || d.status === 'seller_rejected')
  const platformDecides = d.my_role === 'platform' && d.status === 'escalated'

  function submit(e: FormEvent) {
    e.preventDefault()
    if (message.trim().length >= 3) send.mutate()
  }

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <PageHeader title={t('disputes.title')} back={d.my_role === 'seller' ? '/seller' : d.my_role === 'platform' ? '/admin/disputes' : '/orders'} action={<DisputeBadge status={d.status} />} />
      <section className="card space-y-1 p-4 text-sm">
        <p><span className="text-muted">{t('disputes.reasonLabel')}:</span> {t(`disputes.reason.${d.reason}`)}</p>
        <p><span className="text-muted">{t('disputes.orderTotal')}:</span> {formatMoney(d.order_total_cents)}</p>
        <p className="text-muted">{t('disputes.opened', { date: formatDateTime(d.created_at) })}</p>
        {d.status === 'refunded' && d.refund_cents != null && <p className="font-semibold text-success">{t('disputes.refunded', { amount: formatMoney(d.refund_cents) })}</p>}
        {d.resolution_note && <p>{d.resolution_note}</p>}
        <p className="pt-1 text-xs text-muted">{t(`disputes.explain.${d.status}`)}</p>
      </section>

      <section className="space-y-3" aria-label={t('disputes.thread')}>
        {d.messages.map((m) => (
          <div key={m.id} className={`card p-3 text-sm ${m.author_role === d.my_role ? 'ml-8' : 'mr-8'}`}>
            <p className="mb-1 text-xs font-semibold text-muted">{t(`disputes.role.${m.author_role}`)} · {formatDateTime(m.created_at)}</p>
            <p className="whitespace-pre-wrap">{m.body}</p>
          </div>
        ))}
      </section>

      {active && (
        <form onSubmit={submit} className="card space-y-2 p-4">
          <label className="label" htmlFor="dispute-message">{t('disputes.messageLabel')}</label>
          <textarea id="dispute-message" className="input min-h-20" maxLength={2000} value={message} onChange={(e) => setMessage(e.target.value)} />
          {send.isError && <p role="alert" className="text-sm text-danger">{errorMessage(send.error, t)}</p>}
          <button className="btn-secondary" disabled={message.trim().length < 3 || send.isPending}>{t('disputes.send')}</button>
        </form>
      )}

      {(sellerDecides || platformDecides) && (
        <section className="card space-y-4 p-4">
          <h2 className="font-semibold">{t('disputes.decide')}</h2>
          <RefundForm dispute={d} onDone={refresh} />
          {(d.status === 'open' || platformDecides) && (
            <form className="space-y-2 border-t border-line pt-4" onSubmit={(e) => { e.preventDefault(); act.mutate({ path: 'reject', body: { note: rejectNote.trim() } }) }}>
              <label className="label" htmlFor="reject-note">{t('disputes.rejectNote')}</label>
              <input id="reject-note" className="input" maxLength={500} value={rejectNote} onChange={(e) => setRejectNote(e.target.value)} />
              <button className="btn-secondary w-full" disabled={rejectNote.trim().length < 3 || act.isPending}>{t('disputes.reject')}</button>
            </form>
          )}
        </section>
      )}

      {d.my_role === 'buyer' && active && (
        <div className="flex flex-col gap-2 sm:flex-row">
          {d.can_escalate && (
            <button className="btn-primary flex-1" disabled={act.isPending} onClick={() => act.mutate({ path: 'escalate' })}>{t('disputes.escalate')}</button>
          )}
          <button className="btn-ghost flex-1" disabled={act.isPending} onClick={() => window.confirm(t('disputes.cancelConfirm')) && act.mutate({ path: 'cancel' })}>
            {t('disputes.cancel')}
          </button>
        </div>
      )}
      {act.isError && <p role="alert" className="text-sm text-danger">{errorMessage(act.error, t)}</p>}
    </div>
  )
}

interface AdminDispute {
  id: string
  order_id: string
  reason: Dispute['reason']
  status: Dispute['status']
  created_at: string
  order_total_cents: number
  title: string
}

export function AdminDisputesPage() {
  const { t } = useTranslation()
  const list = useQuery({ queryKey: ['admin-disputes'], queryFn: () => api<AdminDispute[]>('/api/admin/disputes') })
  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader title={t('disputes.adminTitle')} back="/me" />
      {list.isPending && <div className="skeleton h-24" />}
      {list.isError && <ErrorState message={errorMessage(list.error, t)} onRetry={() => void list.refetch()} />}
      {list.isSuccess && list.data.length === 0 && <EmptyState icon="shield" title={t('disputes.adminEmpty')} />}
      <ul className="space-y-2">
        {list.data?.map((d) => (
          <li key={d.id}>
            <Link to={`/disputes/${d.id}`} className="card flex items-center justify-between gap-3 p-4 hover:bg-surface-2">
              <span className="min-w-0">
                <span className="block truncate font-semibold">{d.title}</span>
                <span className="text-xs text-muted">{t(`disputes.reason.${d.reason}`)} · {formatDateTime(d.created_at)}</span>
              </span>
              <span className="font-semibold">{formatMoney(d.order_total_cents)}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}
