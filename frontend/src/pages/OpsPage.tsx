import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState, type MouseEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { api, json } from '../api/client'
import { errorMessage } from '../lib/errors'
import { formatDateTime } from '../lib/format'
import { Icon } from '../ui/icons'
import { Badge, ErrorState, PageHeader, Spinner } from '../ui/primitives'

interface Ops {
  window_minutes: number
  traffic: {
    requests: number
    errors_5xx: number
    errors_4xx: number
    error_rate: number
    p50_ms: number | null
    p95_ms: number | null
    per_minute: { minute: string; count: number; errors: number }[]
    routes: { method: string; route: string; count: number; errors_5xx: number; avg_ms: number | null; p95_ms: number | null }[]
  }
  payments: { pending_checkouts: number; stale_attempts: number; webhook_backlog: number; oldest_webhook_s: number | null }
  database: { size_bytes: number; connections: number }
  jobs: { name: string; last_started: string; last_ok_at: string | null; last_error_at: string | null; last_error: string | null; consecutive_failures: number; last_count: number; last_duration_ms: number }[]
  errors: { fingerprint: string; kind: string; route: string; message: string; count: number; first_seen: string; last_seen: string; last_request_id: string | null }[]
  alerts: { key: string; severity: 'warning' | 'critical'; since: string | null }[]
  ai: { provider: string; chat_model: string | null; enabled: boolean; available: boolean; daily_budget_usd: number; calls: number; tokens: number; cost_usd: number }
}

const BARS = 60

function ms(value: number | null): string {
  return value == null ? '—' : value >= 5000 ? '> 2,5 s' : `≤ ${value} ms`
}

function mb(bytes: number): string {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** Alert keys look like "job_failing:payments_sync": a kind and an optional subject. */
function useAlertLabel() {
  const { t } = useTranslation()
  return (key: string) => {
    const [kind, subject] = key.split(':')
    return t(`ops.alert.${kind}`, { name: subject ?? '', defaultValue: key })
  }
}

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="card p-4">
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-1 text-xl font-bold tabular-nums">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-muted">{hint}</p>}
    </div>
  )
}

/** Requests per time slot, stacked: successful (brand) and server errors (error color). */
function TrafficChart({ ops }: { ops: Ops }) {
  const { t } = useTranslation()
  const [hover, setHover] = useState<number | null>(null)
  const slots = useMemo(() => {
    // Fixed slots covering the window, so empty minutes show as gaps instead of disappearing.
    const size = Math.max(1, Math.round(ops.window_minutes / BARS)) * 60_000
    const end = Math.floor(Date.now() / size) * size
    const out = Array.from({ length: BARS }, (_, i) => ({ start: end - (BARS - 1 - i) * size, count: 0, errors: 0 }))
    for (const m of ops.traffic.per_minute) {
      const i = BARS - 1 - Math.floor((end - Math.floor(new Date(m.minute).getTime() / size) * size) / size)
      if (i >= 0 && i < BARS) {
        out[i].count += m.count
        out[i].errors += m.errors
      }
    }
    return out
  }, [ops])
  const max = Math.max(1, ...slots.map((s) => s.count))
  const width = 600
  const height = 140
  const slot = width / BARS
  const bar = Math.max(1, slot - 2)
  const y = (v: number) => (v / max) * (height - 4)
  const time = (ts: number) => new Date(ts).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })

  function onMove(e: MouseEvent<SVGSVGElement>) {
    const box = e.currentTarget.getBoundingClientRect()
    setHover(Math.min(BARS - 1, Math.max(0, Math.floor(((e.clientX - box.left) / box.width) * BARS))))
  }
  const h = hover != null ? slots[hover] : null

  return (
    <section className="card space-y-3 p-4" aria-labelledby="traffic-title">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="traffic-title" className="font-semibold">{t('ops.trafficTitle')}</h2>
        <ul className="flex gap-4 text-xs text-muted">
          <li className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-chart-ok" />{t('ops.ok')}</li>
          <li className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-chart-error" />{t('ops.serverErrors')}</li>
        </ul>
      </div>
      <div className="relative">
        <p className="absolute top-0 left-0 text-[11px] text-muted tabular-nums">{max}</p>
        <svg viewBox={`0 0 ${width} ${height}`} className="h-36 w-full" preserveAspectRatio="none" role="img"
          aria-label={t('ops.chartLabel', { count: ops.traffic.requests, minutes: ops.window_minutes })}
          onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
          <line x1="0" x2={width} y1={height - 0.5} y2={height - 0.5} className="stroke-line" strokeWidth="1" />
          {slots.map((s, i) => {
            const x = i * slot + 1
            const ok = y(s.count - s.errors)
            const err = y(s.errors)
            return (
              <g key={s.start} opacity={hover == null || hover === i ? 1 : 0.55}>
                {ok > 0 && <rect x={x} y={height - ok} width={bar} height={ok} rx="2" className="fill-chart-ok" />}
                {err > 0 && <rect x={x} y={height - ok - err - (ok > 0 ? 2 : 0)} width={bar} height={err} rx="2" className="fill-chart-error" />}
              </g>
            )
          })}
        </svg>
        {h && hover != null && (
          <div className="pointer-events-none absolute top-6 z-10 rounded-xl bg-surface px-3 py-2 text-xs shadow-lg ring-1 ring-line"
            style={{ left: `${Math.min(80, Math.max(0, (hover / BARS) * 100 - 10))}%` }}>
            <p className="font-semibold">{time(h.start)}</p>
            <p>{t('ops.requests')}: <span className="tabular-nums">{h.count}</span></p>
            <p>{t('ops.serverErrors')}: <span className="tabular-nums">{h.errors}</span></p>
          </div>
        )}
        <div className="flex justify-between text-[11px] text-muted"><span>{time(slots[0].start)}</span><span>{time(slots[BARS - 1].start)}</span></div>
      </div>
      <details className="text-sm">
        <summary className="cursor-pointer text-muted">{t('ops.asTable')}</summary>
        <table className="mt-2 w-full text-left text-xs">
          <thead><tr className="text-muted"><th className="py-1">{t('ops.time')}</th><th>{t('ops.requests')}</th><th>{t('ops.serverErrors')}</th></tr></thead>
          <tbody>
            {slots.filter((s) => s.count > 0).map((s) => (
              <tr key={s.start} className="border-t border-line"><td className="py-1">{time(s.start)}</td><td className="tabular-nums">{s.count}</td><td className="tabular-nums">{s.errors}</td></tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  )
}

function ErrorRow({ e }: { e: Ops['errors'][number] }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const detail = useQuery({
    queryKey: ['ops-error', e.fingerprint],
    queryFn: () => api<{ traceback: string }>(`/api/admin/errors/${e.fingerprint}`),
    enabled: open,
  })
  return (
    <li className="space-y-1 p-3 text-sm">
      <button type="button" className="flex w-full items-start justify-between gap-3 text-left" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="min-w-0">
          <span className="block font-semibold">{e.kind} <span className="font-normal text-muted">· {e.route}</span></span>
          <span className="block truncate text-muted">{e.message}</span>
        </span>
        <span className="shrink-0 text-right text-xs text-muted">
          <span className="block font-semibold text-fg tabular-nums">{t('ops.times', { count: e.count })}</span>
          {formatDateTime(e.last_seen)}
        </span>
      </button>
      {open && (detail.data ? (
        <pre className="max-h-64 overflow-auto rounded-xl bg-surface-2 p-3 text-[11px] leading-snug">{detail.data.traceback}</pre>
      ) : <Spinner className="text-primary" />)}
    </li>
  )
}

export function OpsPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const label = useAlertLabel()
  const [minutes, setMinutes] = useState(60)
  const ops = useQuery({
    queryKey: ['ops', minutes],
    queryFn: () => api<Ops>(`/api/admin/ops?minutes=${minutes}`),
    refetchInterval: 30_000,
  })
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api(`/api/admin/flags/ai`, { method: 'PUT', body: json({ enabled }) }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['ops'] }),
  })

  if (ops.isPending) return <div className="flex justify-center py-16 text-primary"><Spinner /></div>
  if (ops.isError) return <ErrorState message={errorMessage(ops.error, t)} onRetry={() => void ops.refetch()} />
  const o = ops.data

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <PageHeader title={t('ops.title')} back="/me" action={
        <select className="input min-h-10 w-auto py-1.5 text-sm" value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} aria-label={t('ops.window')}>
          <option value={60}>{t('ops.lastHour')}</option>
          <option value={360}>{t('ops.last6h')}</option>
          <option value={1440}>{t('ops.last24h')}</option>
        </select>
      } />

      <section aria-label={t('ops.alertsTitle')}>
        {o.alerts.length === 0 ? (
          <p className="flex items-center gap-2 rounded-2xl bg-success-soft px-4 py-3 text-sm text-success"><Icon name="check" size={18} />{t('ops.allGood')}</p>
        ) : (
          <ul className="space-y-2">
            {o.alerts.map((a) => (
              <li key={a.key} className={`flex items-center gap-2 rounded-2xl px-4 py-3 text-sm ${a.severity === 'critical' ? 'bg-danger-soft text-danger' : 'bg-warning-soft text-warning-fg'}`}>
                <Icon name="info" size={18} />
                <span className="font-semibold">{t(`ops.severity.${a.severity}`)}:</span> {label(a.key)}
                {a.since && <span className="ml-auto text-xs opacity-80">{formatDateTime(a.since)}</span>}
              </li>
            ))}
          </ul>
        )}
      </section>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile label={t('ops.requests')} value={String(o.traffic.requests)} hint={t('ops.clientErrors', { count: o.traffic.errors_4xx })} />
        <Tile label={t('ops.errorRate')} value={`${(o.traffic.error_rate * 100).toFixed(1)}%`} hint={t('ops.serverErrorsCount', { count: o.traffic.errors_5xx })} />
        <Tile label="p50" value={ms(o.traffic.p50_ms)} hint={`p95 ${ms(o.traffic.p95_ms)}`} />
        <Tile label={t('ops.database')} value={mb(o.database.size_bytes)} hint={t('ops.connections', { count: o.database.connections })} />
        <Tile label={t('ops.pendingCheckouts')} value={String(o.payments.pending_checkouts)} hint={t('ops.staleAttempts', { count: o.payments.stale_attempts })} />
        <Tile label={t('ops.webhookBacklog')} value={String(o.payments.webhook_backlog)}
          hint={o.payments.oldest_webhook_s != null ? t('ops.oldest', { s: Math.round(o.payments.oldest_webhook_s) }) : undefined} />
        <Tile label={t('ops.aiCalls')} value={String(o.ai.calls)} hint={t('ops.aiCost', { cost: o.ai.cost_usd.toFixed(4) })} />
        <Tile label={t('ops.jobs')} value={String(o.jobs.length)} hint={t('ops.failingJobs', { count: o.jobs.filter((j) => j.consecutive_failures > 0).length })} />
      </div>

      <TrafficChart ops={o} />

      <section className="card overflow-x-auto p-4" aria-labelledby="routes-title">
        <h2 id="routes-title" className="mb-2 font-semibold">{t('ops.routes')}</h2>
        <table className="w-full min-w-[520px] text-left text-sm">
          <thead><tr className="text-xs text-muted"><th className="py-1.5">{t('ops.route')}</th><th className="text-right">{t('ops.requests')}</th><th className="text-right">5xx</th><th className="text-right">{t('ops.avg')}</th><th className="text-right">p95</th></tr></thead>
          <tbody>
            {o.traffic.routes.map((r) => (
              <tr key={`${r.method} ${r.route}`} className="border-t border-line">
                <td className="py-1.5 font-mono text-xs">{r.method} {r.route}</td>
                <td className="text-right tabular-nums">{r.count}</td>
                <td className={`text-right tabular-nums ${r.errors_5xx ? 'font-semibold text-danger' : ''}`}>{r.errors_5xx}</td>
                <td className="text-right tabular-nums">{r.avg_ms != null ? `${r.avg_ms} ms` : '—'}</td>
                <td className="text-right tabular-nums">{ms(r.p95_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <div className="grid gap-5 lg:grid-cols-2">
        <section className="card p-4" aria-labelledby="jobs-title">
          <h2 id="jobs-title" className="mb-2 font-semibold">{t('ops.jobs')}</h2>
          <ul className="divide-y divide-line text-sm">
            {o.jobs.map((j) => (
              <li key={j.name} className="flex items-center justify-between gap-3 py-2">
                <span className="min-w-0">
                  <span className="block font-mono text-xs">{j.name}</span>
                  {j.last_error && j.consecutive_failures > 0 && <span className="block truncate text-xs text-danger">{j.last_error}</span>}
                </span>
                <span className="flex shrink-0 items-center gap-2 text-xs text-muted">
                  {Math.round(j.last_duration_ms)} ms
                  <Badge tone={j.consecutive_failures ? 'danger' : 'success'}>{j.consecutive_failures ? t('ops.failing', { count: j.consecutive_failures }) : t('ops.jobOk')}</Badge>
                </span>
              </li>
            ))}
          </ul>
        </section>

        <section className="card space-y-3 p-4" aria-labelledby="ai-title">
          <h2 id="ai-title" className="font-semibold">{t('ops.aiTitle')}</h2>
          <p className="text-sm text-muted">
            {o.ai.provider === 'none' ? t('ops.aiOff') : t('ops.aiProvider', { provider: o.ai.provider, model: o.ai.chat_model })}
          </p>
          <p className="text-sm">{t('ops.aiUsage', { calls: o.ai.calls, tokens: o.ai.tokens, cost: o.ai.cost_usd.toFixed(4) })}</p>
          <label className="flex items-center gap-3 text-sm">
            <input type="checkbox" className="h-5 w-5" checked={o.ai.enabled} disabled={toggle.isPending} onChange={(e) => toggle.mutate(e.target.checked)} />
            {t('ops.aiSwitch')}
          </label>
          <Badge tone={o.ai.available ? 'success' : 'neutral'}>{o.ai.available ? t('ops.aiAvailable') : t('ops.aiUnavailable')}</Badge>
        </section>
      </div>

      <section className="card" aria-labelledby="errors-title">
        <h2 id="errors-title" className="p-4 pb-0 font-semibold">{t('ops.errors')}</h2>
        {o.errors.length === 0 ? <p className="p-4 text-sm text-muted">{t('ops.noErrors')}</p> : (
          <ul className="divide-y divide-line">{o.errors.map((e) => <ErrorRow key={e.fingerprint} e={e} />)}</ul>
        )}
      </section>
    </div>
  )
}
