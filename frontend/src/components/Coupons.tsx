import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { api, json } from '../api/client'
import { errorMessage } from '../lib/errors'
import { formatMoney, parseMoney } from '../lib/format'
import { Icon } from '../ui/icons'
import { Badge } from '../ui/primitives'

interface Coupon {
  id: string
  code: string
  kind: 'percent' | 'fixed'
  value: number
  min_items_cents: number
  max_uses: number | null
  uses: number
  expires_at: string | null
  active: boolean
}

/** The seller's coupons: create, see usage, switch off. */
export function Coupons() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const coupons = useQuery({ queryKey: ['coupons'], queryFn: () => api<Coupon[]>('/api/me/coupons') })
  const refresh = () => void qc.invalidateQueries({ queryKey: ['coupons'] })
  const [code, setCode] = useState('')
  const [kind, setKind] = useState<'percent' | 'fixed'>('percent')
  const [value, setValue] = useState('10')
  const [minimum, setMinimum] = useState('')
  const [maxUses, setMaxUses] = useState('')
  const [expires, setExpires] = useState('')
  const create = useMutation({
    mutationFn: () =>
      api<Coupon>('/api/me/coupons', {
        method: 'POST',
        body: json({
          code: code.trim(),
          kind,
          value: kind === 'percent' ? Number(value) : parseMoney(value),
          min_items_cents: parseMoney(minimum || '0') ?? 0,
          max_uses: maxUses ? Number(maxUses) : null,
          expires_at: expires ? new Date(`${expires}T23:59:59`).toISOString() : null,
        }),
      }),
    onSuccess: () => { setCode(''); refresh() },
  })
  const toggle = useMutation({
    mutationFn: (c: Coupon) => api<Coupon>(`/api/me/coupons/${c.id}`, { method: 'PATCH', body: json({ active: !c.active }) }),
    onSuccess: refresh,
  })

  function submit(e: FormEvent) {
    e.preventDefault()
    create.mutate()
  }

  return (
    <section className="space-y-3" aria-labelledby="coupons-title">
      <h2 id="coupons-title" className="font-semibold">{t('coupons.title')}</h2>
      <form onSubmit={submit} className="card grid gap-3 p-4 sm:grid-cols-3">
        <div>
          <label className="label" htmlFor="coupon-code">{t('coupons.code')}</label>
          <input id="coupon-code" className="input uppercase" required minLength={3} maxLength={20} pattern="[A-Za-z0-9_-]+" value={code} onChange={(e) => setCode(e.target.value)} placeholder="FRETE10" />
        </div>
        <div>
          <label className="label" htmlFor="coupon-kind">{t('coupons.kind')}</label>
          <select id="coupon-kind" className="input" value={kind} onChange={(e) => setKind(e.target.value as 'percent' | 'fixed')}>
            <option value="percent">{t('coupons.percent')}</option>
            <option value="fixed">{t('coupons.fixed')}</option>
          </select>
        </div>
        <div>
          <label className="label" htmlFor="coupon-value">{kind === 'percent' ? t('coupons.valuePercent') : t('coupons.valueFixed')}</label>
          <input id="coupon-value" className="input" inputMode="decimal" required value={value} onChange={(e) => setValue(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="coupon-min">{t('coupons.minimum')}</label>
          <input id="coupon-min" className="input" inputMode="decimal" value={minimum} onChange={(e) => setMinimum(e.target.value)} placeholder="0,00" />
        </div>
        <div>
          <label className="label" htmlFor="coupon-max">{t('coupons.maxUses')}</label>
          <input id="coupon-max" className="input" type="number" min={1} value={maxUses} onChange={(e) => setMaxUses(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="coupon-expires">{t('coupons.expires')}</label>
          <input id="coupon-expires" className="input" type="date" value={expires} onChange={(e) => setExpires(e.target.value)} />
        </div>
        {create.isError && <p role="alert" className="text-sm text-danger sm:col-span-3">{errorMessage(create.error, t)}</p>}
        <button className="btn-primary sm:col-span-3" disabled={create.isPending}><Icon name="plus" size={18} />{t('coupons.create')}</button>
      </form>
      {coupons.data && coupons.data.length > 0 && (
        <ul className="card divide-y divide-line">
          {coupons.data.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center gap-3 p-3 text-sm">
              <code className="rounded-lg bg-surface-2 px-2 py-1 font-mono font-semibold">{c.code}</code>
              <span>{c.kind === 'percent' ? `${c.value}%` : formatMoney(c.value)}</span>
              <span className="text-muted">{t('coupons.uses', { uses: c.uses, max: c.max_uses ?? '∞' })}</span>
              <span className="ml-auto flex items-center gap-2">
                <Badge tone={c.active ? 'success' : 'neutral'}>{c.active ? t('coupons.active') : t('coupons.inactive')}</Badge>
                <button type="button" className="btn-ghost min-h-9 px-2 text-sm" disabled={toggle.isPending} onClick={() => toggle.mutate(c)}>
                  {c.active ? t('coupons.deactivate') : t('coupons.activate')}
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
