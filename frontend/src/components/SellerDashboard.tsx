import { useEffect, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import {
  type DayPoint,
  type Metric,
  type SellerAnalytics,
  METRICS,
  PERIODS,
  analyticsCsvUrl,
  useSellerAnalytics,
} from '../api/analytics'
import { currentLanguage } from '../i18n'
import { errorMessage } from '../lib/errors'
import { formatMoney } from '../lib/format'
import { Icon } from '../ui/icons'

const locale = () => (currentLanguage() === 'en' ? 'en-US' : 'pt-BR')
const formatCount = (n: number) => n.toLocaleString(locale())
const formatPercent = (ratio: number) =>
  ratio.toLocaleString(locale(), { style: 'percent', maximumFractionDigits: 1 })
const formatValue = (metric: Metric, n: number) =>
  metric === 'revenue_cents' ? formatMoney(n) : formatCount(n)
/** Days come as YYYY-MM-DD (Brasília days); show them without a time zone shift. */
const formatDay = (iso: string, long = false) =>
  new Date(`${iso}T12:00:00Z`).toLocaleDateString(locale(), {
    timeZone: 'UTC',
    day: 'numeric',
    month: 'short',
    ...(long ? { weekday: 'short' } : {}),
  })

function useFormatMinutes() {
  const { t } = useTranslation()
  return (minutes: number) => {
    if (minutes < 60) return t('analytics.minutes', { count: Math.max(1, Math.round(minutes)) })
    if (minutes < 48 * 60) return t('analytics.hours', { value: (minutes / 60).toLocaleString(locale(), { maximumFractionDigits: 1 }) })
    return t('analytics.days', { value: (minutes / 1440).toLocaleString(locale(), { maximumFractionDigits: 1 }) })
  }
}

/** A round top for the axis, at least the largest value, whose half is round too. */
function niceMax(value: number): number {
  if (value <= 10) return Math.max(2, Math.ceil(value / 2) * 2)
  const power = 10 ** Math.floor(Math.log10(value))
  return Math.round(([1, 1.2, 1.6, 2, 3, 4, 5, 6, 8, 10].find((step) => step * power >= value) ?? 10) * power)
}

function MetricTile({ metric, value, active, onSelect, hint }: {
  metric: Metric
  value: number
  active: boolean
  onSelect: () => void
  hint?: string
}) {
  const { t } = useTranslation()
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onSelect}
      className={`card flex flex-col justify-start p-3 text-left transition sm:p-4 ${active ? 'ring-2 ring-chart-series' : 'hover:shadow-lift'}`}
    >
      <span className="flex items-center gap-1.5 text-xs font-medium text-muted">
        <span aria-hidden className={`h-2 w-2 rounded-full ${active ? 'bg-chart-series' : 'bg-line'}`} />
        {t(`analytics.metric.${metric}`)}
      </span>
      <span className="mt-1 block text-lg font-bold tabular-nums sm:text-xl">{formatValue(metric, value)}</span>
      {hint && <span className="mt-0.5 block text-xs text-muted">{hint}</span>}
    </button>
  )
}

interface Bucket {
  start: string
  end: string
  value: number
}

/** Days, or weeks ending on the last day when a bar per day would be too thin to read. */
function toBuckets(series: DayPoint[], metric: Metric, weekly: boolean): Bucket[] {
  if (!weekly) return series.map((d) => ({ start: d.date, end: d.date, value: d[metric] }))
  const buckets: Bucket[] = []
  for (let end = series.length; end > 0; end -= 7) {
    const days = series.slice(Math.max(0, end - 7), end)
    buckets.unshift({ start: days[0].date, end: days[days.length - 1].date, value: days.reduce((sum, d) => sum + d[metric], 0) })
  }
  return buckets
}

function useWidth() {
  const [width, setWidth] = useState(0)
  const [node, setNode] = useState<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!node) return
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    observer.observe(node)
    return () => observer.disconnect()
  }, [node])
  return [setNode, width] as const
}

function DailyChart({ series, metric }: { series: DayPoint[]; metric: Metric }) {
  const { t } = useTranslation()
  const [active, setActive] = useState<number | null>(null)
  const [asTable, setAsTable] = useState(false)
  const [measure, width] = useWidth()
  // A bar narrower than about 6px (after the 2px gaps) stops being readable: switch to weeks.
  const weekly = series.length > 31 && width > 0 && width / series.length < 8
  const buckets = toBuckets(series, metric, weekly)
  const top = niceMax(Math.max(...buckets.map((b) => b.value), 0))
  const total = buckets.reduce((sum, b) => sum + b.value, 0)
  const label = t(`analytics.metric.${metric}`)
  const point = active !== null ? buckets[active] : null
  const ticks = [top, top / 2, 0]
  const n = buckets.length
  // Date labels under the axis: first, middle and last bar.
  const marks = [...new Set([0, Math.floor((n - 1) / 2), n - 1])]
  const when = (b: Bucket, long = false) =>
    b.start === b.end ? formatDay(b.start, long) : `${formatDay(b.start)} – ${formatDay(b.end)}`

  const onKey = (e: KeyboardEvent) => {
    const last = n - 1
    const current = active ?? last
    const next = e.key === 'ArrowLeft' ? current - 1 : e.key === 'ArrowRight' ? current + 1 : e.key === 'Home' ? 0 : e.key === 'End' ? last : null
    if (next === null) return
    e.preventDefault()
    setActive(Math.min(last, Math.max(0, next)))
  }

  return (
    <section className="card space-y-3 p-4" aria-labelledby="daily-chart-title">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="daily-chart-title" className="font-semibold">{t(weekly ? 'analytics.perWeek' : 'analytics.perDay', { metric: label })}</h3>
        <button type="button" className="btn-ghost min-h-8 px-2 text-xs" aria-pressed={asTable} onClick={() => setAsTable((v) => !v)}>
          <Icon name={asTable ? 'chart' : 'list'} size={16} />{asTable ? t('analytics.showChart') : t('analytics.showTable')}
        </button>
      </header>
      {asTable ? (
        <div className="max-h-80 overflow-y-auto" tabIndex={0}>
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-surface text-left text-xs text-muted">
              <tr><th className="py-1 font-medium">{t(weekly ? 'analytics.week' : 'analytics.day')}</th><th className="py-1 text-right font-medium">{label}</th></tr>
            </thead>
            <tbody>
              {[...buckets].reverse().map((b) => (
                <tr key={b.start} className="border-t border-line">
                  <td className="py-1.5">{when(b, true)}</td>
                  <td className="py-1.5 text-right tabular-nums">{formatValue(metric, b.value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="relative">
          <div className="flex gap-2">
            {/* y axis: three recessive ticks */}
            <div aria-hidden className="flex h-44 shrink-0 flex-col justify-between text-right text-[11px] leading-none text-muted tabular-nums sm:h-52">
              {ticks.map((v) => <span key={v}>{metric === 'revenue_cents' ? formatMoney(v).replace(/[,.]00$/, '') : formatCount(v)}</span>)}
            </div>
            <div ref={measure} className="min-w-0 flex-1">
              <div
                role="img"
                tabIndex={0}
                aria-label={t(weekly ? 'analytics.chartLabelWeekly' : 'analytics.chartLabel', { metric: label, days: series.length, total: formatValue(metric, total) })}
                onKeyDown={onKey}
                onBlur={() => setActive(null)}
                onMouseLeave={() => setActive(null)}
                className="relative flex h-44 items-end gap-[2px] rounded-md sm:h-52"
              >
                <div aria-hidden className="pointer-events-none absolute inset-0 flex flex-col justify-between">
                  {ticks.map((v) => <div key={v} className="border-t border-line" />)}
                </div>
                {buckets.map((b, i) => {
                  const height = b.value > 0 ? Math.max(2, (b.value / top) * 100) : 0
                  return (
                    <div key={b.start} className="relative flex h-full min-w-0 flex-1 items-end" onMouseEnter={() => setActive(i)}>
                      <div
                        className={`w-full rounded-t-[4px] transition-opacity ${active === null || active === i ? 'opacity-100' : 'opacity-40'} bg-chart-series`}
                        style={{ height: `${height}%` }}
                      />
                    </div>
                  )
                })}
              </div>
              <div aria-hidden className="relative mt-1 h-4 text-[11px] text-muted">
                {marks.map((i) => (
                  <span
                    key={i}
                    className="absolute whitespace-nowrap"
                    style={i === 0 ? { left: 0 } : i === n - 1 ? { right: 0 } : { left: `${((i + 0.5) / n) * 100}%`, transform: 'translateX(-50%)' }}
                  >
                    {formatDay(i === n - 1 ? buckets[i].end : buckets[i].start)}
                  </span>
                ))}
              </div>
            </div>
          </div>
          {point && active !== null && (
            <div
              role="status"
              className="pointer-events-none absolute top-0 z-10 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs shadow-lift"
              style={active > n / 2 ? { right: `${((n - active - 1) / n) * 100}%` } : { left: `calc(2.5rem + ${(active / n) * 100}%)` }}
            >
              <p className="text-muted">{when(point, true)}</p>
              <p className="font-semibold tabular-nums">{label}: {formatValue(metric, point.value)}</p>
            </div>
          )}
        </div>
      )}
    </section>
  )
}

function ListingTable({ data }: { data: SellerAnalytics }) {
  const { t } = useTranslation()
  const [all, setAll] = useState(false)
  const rows = all ? data.listings : data.listings.slice(0, 8)
  const columns: Metric[] = ['views', 'saves', 'questions', 'conversations', 'orders', 'revenue_cents']
  return (
    <section className="card space-y-3 p-4" aria-labelledby="per-listing-title">
      <div>
        <h3 id="per-listing-title" className="font-semibold">{t('analytics.perListing')}</h3>
        <p className="text-xs text-muted">{t('analytics.perListingHint')}</p>
      </div>
      <div className="-mx-4 overflow-x-auto px-4" tabIndex={0}>
        <table className="w-full min-w-[40rem] text-sm">
          <thead className="text-left text-xs text-muted">
            <tr>
              <th scope="col" className="sticky left-0 bg-surface py-1.5 pr-3 font-medium">{t('analytics.listing')}</th>
              {columns.map((m) => <th key={m} scope="col" className="px-2 py-1.5 text-right font-medium">{t(`analytics.metric.${m}`)}</th>)}
              <th scope="col" className="py-1.5 pl-2 text-right font-medium">{t('analytics.conversion')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.listing_id} className="border-t border-line">
                <th scope="row" className="sticky left-0 max-w-[12rem] bg-surface py-2 pr-3 text-left font-normal">
                  <Link to={`/listings/${r.listing_id}`} className="line-clamp-2 hover:underline">{r.title}</Link>
                  {r.status !== 'active' && <span className="text-xs text-muted">{t(`listing.status.${r.status}`, { defaultValue: r.status })}</span>}
                </th>
                {columns.map((m) => (
                  <td key={m} className={`px-2 py-2 text-right tabular-nums ${r[m] === 0 ? 'text-muted' : ''}`}>{formatValue(m, r[m])}</td>
                ))}
                <td className="py-2 pl-2 text-right tabular-nums">{r.conversion === null ? '—' : formatPercent(r.conversion)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.listings.length > 8 && (
        <button type="button" className="btn-ghost min-h-9 px-2 text-sm" onClick={() => setAll((v) => !v)}>
          {all ? t('analytics.showFewer') : t('analytics.showAll', { count: data.listings.length })}
        </button>
      )}
    </section>
  )
}

function ResponseCard({ data }: { data: SellerAnalytics }) {
  const { t } = useTranslation()
  const formatMinutes = useFormatMinutes()
  const { conversations, answered, median_minutes } = data.response
  return (
    <section className="card space-y-1 p-4">
      <h3 className="text-sm font-medium text-muted">{t('analytics.responseTitle')}</h3>
      <p className="text-xl font-bold">{median_minutes === null ? '—' : formatMinutes(median_minutes)}</p>
      <p className="text-xs text-muted">
        {conversations === 0 ? t('analytics.noConversations') : t('analytics.answered', { answered, count: conversations })}
      </p>
    </section>
  )
}

function CouponCard({ data }: { data: SellerAnalytics }) {
  const { t } = useTranslation()
  const c = data.coupons
  const revenue = c.revenue_with_coupon_cents + c.revenue_without_coupon_cents
  const share = revenue > 0 ? c.revenue_with_coupon_cents / revenue : 0
  return (
    <section className="card space-y-2 p-4">
      <h3 className="text-sm font-medium text-muted">{t('analytics.couponsTitle')}</h3>
      {c.orders_with_coupon === 0 ? (
        <p className="text-sm text-muted">{t('analytics.noCouponOrders')}</p>
      ) : (
        <>
          <p className="text-xl font-bold">{formatPercent(share)}</p>
          <p className="text-xs text-muted">
            {t('analytics.couponShare', { count: c.orders_with_coupon, revenue: formatMoney(c.revenue_with_coupon_cents), discount: formatMoney(c.discount_cents) })}
          </p>
          <ul className="space-y-1 text-sm">
            {c.by_code.map((row) => (
              <li key={row.code} className="flex justify-between gap-2">
                <code className="font-mono">{row.code}</code>
                <span className="tabular-nums text-muted">{t('analytics.couponRow', { count: row.orders, revenue: formatMoney(row.revenue_cents) })}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}

export function SellerDashboard() {
  const { t } = useTranslation()
  const [days, setDays] = useState<number>(30)
  const [metric, setMetric] = useState<Metric>('views')
  const query = useSellerAnalytics(days)
  const data = query.data
  const formatMinutes = useFormatMinutes()

  const hints: Partial<Record<Metric, string>> = data
    ? {
        conversations: data.response.median_minutes !== null ? t('analytics.answersIn', { time: formatMinutes(data.response.median_minutes) }) : undefined,
        orders: data.totals.conversion !== null ? t('analytics.conversionHint', { value: formatPercent(data.totals.conversion) }) : undefined,
        revenue_cents: data.totals.refunds > 0 ? t('analytics.refunds', { count: data.totals.refunds }) : undefined,
      }
    : {}

  return (
    <section className="space-y-3" aria-labelledby="analytics-title">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="analytics-title" className="font-semibold">{t('analytics.title')}</h2>
        <div className="flex items-center gap-2">
          <div role="group" aria-label={t('analytics.period')} className="flex rounded-full bg-surface-2 p-0.5 text-xs font-semibold">
            {PERIODS.map((p) => (
              <button
                key={p}
                type="button"
                aria-pressed={days === p}
                onClick={() => setDays(p)}
                className={`min-h-8 rounded-full px-3 transition ${days === p ? 'bg-surface text-fg shadow-card' : 'text-muted hover:text-fg'}`}
              >
                {t('analytics.daysShort', { count: p })}
              </button>
            ))}
          </div>
          <a href={analyticsCsvUrl(days)} download aria-label={t('analytics.downloadCsv')} className="btn-secondary min-h-9 rounded-full px-3 text-xs">
            <Icon name="download" size={16} />CSV
          </a>
        </div>
      </header>
      {query.isPending && <div className="skeleton h-72" />}
      {query.isError && <p role="alert" className="text-sm text-danger">{errorMessage(query.error, t)}</p>}
      {data && (
        <div className={`space-y-3 transition-opacity ${query.isPlaceholderData ? 'opacity-60' : ''}`}>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 sm:gap-3 lg:grid-cols-6" role="group" aria-label={t('analytics.pickMetric')}>
            {METRICS.map((m) => (
              <MetricTile key={m} metric={m} value={data.totals[m]} active={metric === m} onSelect={() => setMetric(m)} hint={hints[m]} />
            ))}
          </div>
          <DailyChart series={data.series} metric={metric} />
          <div className="grid gap-3 sm:grid-cols-2">
            <ResponseCard data={data} />
            <CouponCard data={data} />
          </div>
          {data.listings.length > 0 && <ListingTable data={data} />}
        </div>
      )}
    </section>
  )
}
