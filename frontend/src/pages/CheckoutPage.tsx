import { CardPayment, initMercadoPago } from '@mercadopago/sdk-react'
import { Elements, PaymentElement, useElements, useStripe } from '@stripe/react-stripe-js'
import { loadStripe, type Stripe } from '@stripe/stripe-js'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { api, json } from '../api/client'
import type { Attempt, Checkout } from '../api/orders'
import { currentLanguage } from '../i18n'
import { errorMessage } from '../lib/errors'
import { formatMoney } from '../lib/format'
import { Icon } from '../ui/icons'
import { ErrorState, Spinner } from '../ui/primitives'

const stripePromises = new Map<string, Promise<Stripe | null>>()
function stripeFor(key: string) {
  if (!stripePromises.has(key)) stripePromises.set(key, loadStripe(key))
  return stripePromises.get(key)!
}

let mercadoPagoKey: string | null = null
function initMp(key: string) {
  if (mercadoPagoKey !== key) {
    initMercadoPago(key, { locale: currentLanguage() === 'en' ? 'en-US' : 'pt-BR' })
    mercadoPagoKey = key
  }
}

function useCountdown(until: string) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  const left = Math.max(0, new Date(until).getTime() - now)
  return { left, label: `${Math.floor(left / 60000)}:${String(Math.floor((left % 60000) / 1000)).padStart(2, '0')}` }
}

function useStartPayment(checkoutId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: unknown) => api<Checkout>(`/api/checkouts/${checkoutId}/payments`, { method: 'POST', body: json(body) }),
    onSuccess: (data) => qc.setQueryData(['checkout', checkoutId], data),
  })
}

function TestHint({ children }: { children: ReactNode }) {
  return <p className="flex gap-2 rounded-xl bg-warning-soft px-3 py-2 text-xs text-warning-fg"><Icon name="info" size={16} className="shrink-0" />{children}</p>
}

export function CheckoutPage() {
  const { t } = useTranslation()
  const { id = '' } = useParams()
  const qc = useQueryClient()
  const checkout = useQuery({
    queryKey: ['checkout', id],
    queryFn: () => api<Checkout>(`/api/checkouts/${id}`),
    // While unpaid, poll: the provider confirms the payment, not this page.
    refetchInterval: (query) => (query.state.data?.status === 'pending' ? 3000 : false),
  })
  const cancel = useMutation({
    mutationFn: () => api<Checkout>(`/api/checkouts/${id}/cancel`, { method: 'POST' }),
    onSuccess: (data) => qc.setQueryData(['checkout', id], data),
  })

  if (checkout.isPending) return <div className="mx-auto max-w-4xl space-y-3"><div className="skeleton h-20" /><div className="skeleton h-72" /></div>
  if (checkout.isError) return <ErrorState message={errorMessage(checkout.error, t)} onRetry={() => void checkout.refetch()} />
  const data = checkout.data

  return (
    <div className="mx-auto grid max-w-4xl gap-5 lg:grid-cols-[1fr_340px] lg:items-start">
      <section className="space-y-4">
        {data.status === 'pending' && <PendingHeader checkout={data} />}
        {data.status === 'paid' && (
          <div className="card space-y-3 border-success/40 bg-success-soft p-6 text-center">
            <span className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-success text-white"><Icon name="check" size={30} /></span>
            <h1 className="text-xl font-bold">{t('checkout.paidTitle')}</h1>
            <p className="text-sm">{t('checkout.paidText')}</p>
            <Link to="/orders" className="btn-primary">{t('checkout.seeOrders')}</Link>
          </div>
        )}
        {(data.status === 'expired' || data.status === 'cancelled') && (
          <div className="card space-y-3 p-6 text-center">
            <h1 className="text-xl font-bold">{data.status === 'expired' ? t('checkout.expiredTitle') : t('checkout.cancelledTitle')}</h1>
            <p className="text-sm text-muted">{t('checkout.closedText')}</p>
            <Link to="/" className="btn-secondary">{t('listing.backToListings')}</Link>
          </div>
        )}
        {data.status === 'pending' && data.provider === 'stripe' && <StripePayment checkout={data} />}
        {data.status === 'pending' && data.provider === 'mercadopago' && <MercadoPagoPayment checkout={data} />}
        {data.status === 'pending' && data.provider === 'fake' && <FakePayment checkout={data} />}
        {data.status === 'pending' && (
          <button className="btn-ghost min-h-9 px-2 text-sm text-muted" onClick={() => cancel.mutate()} disabled={cancel.isPending}>{t('checkout.cancel')}</button>
        )}
      </section>
      <Summary checkout={data} />
    </div>
  )
}

function PendingHeader({ checkout }: { checkout: Checkout }) {
  const { t } = useTranslation()
  const { left, label } = useCountdown(checkout.expires_at)
  return (
    <div className="flex items-center justify-between gap-4">
      <h1 className="text-xl font-bold">{t('checkout.payment')}</h1>
      <span className={`rounded-full px-3 py-1 text-sm font-semibold tabular-nums ${left < 120_000 ? 'bg-danger-soft text-danger' : 'bg-surface-2 text-muted'}`}>
        {t('checkout.reservedFor', { time: label })}
      </span>
    </div>
  )
}

function Summary({ checkout }: { checkout: Checkout }) {
  const { t } = useTranslation()
  return (
    <aside className="card divide-y divide-line text-sm lg:sticky lg:top-24">
      {checkout.orders.map((order) => (
        <div key={order.id} className="space-y-1.5 p-4">
          <p className="text-xs text-muted">{t('cart.soldBy', { name: order.seller.display_name })}</p>
          {order.items.map((item) => (
            <div key={`${item.listing_id}-${item.variant_name ?? ''}`} className="flex justify-between gap-2"><span>{item.quantity} × {item.title}{item.variant_name ? ` (${item.variant_name})` : ''}</span><span>{formatMoney(item.unit_price_cents * item.quantity)}</span></div>
          ))}
          <div className="flex justify-between text-muted"><span>{t('cart.shipping')}</span><span>{formatMoney(order.shipping_cents)}</span></div>
        </div>
      ))}
      <div className="flex justify-between p-4 text-base font-bold"><span>{t('cart.total')}</span><span>{formatMoney(checkout.total_cents)}</span></div>
    </aside>
  )
}

// --- Stripe -----------------------------------------------------------------------------------

function StripePayment({ checkout }: { checkout: Checkout }) {
  const { t } = useTranslation()
  const start = useStartPayment(checkout.id)
  const attempt = checkout.attempts.find((a) => a.method === 'element' && a.status === 'pending')
  const secret = attempt?.client_data.client_secret
  const key = checkout.provider_config.publishable_key
  const dark = document.documentElement.classList.contains('dark')

  useEffect(() => {
    if (!attempt && !start.isPending && !start.isError) start.mutate({ method: 'element' })
  }, [attempt, start])

  if (start.isError) return <ErrorState message={errorMessage(start.error, t)} />
  if (!secret || !key) return <div className="card flex items-center gap-2 p-5 text-sm text-muted"><Spinner className="text-primary" />{t('checkout.preparing')}</div>
  return (
    <Elements stripe={stripeFor(key)} options={{
      clientSecret: secret,
      locale: currentLanguage() === 'en' ? 'en' : 'pt-BR',
      appearance: { theme: dark ? 'night' : 'stripe', variables: { colorPrimary: dark ? '#3cc9b6' : '#0b6b62', borderRadius: '12px' } },
    }}>
      <StripeForm checkoutId={checkout.id} total={checkout.total_cents} />
    </Elements>
  )
}

function StripeForm({ checkoutId, total }: { checkoutId: string; total: number }) {
  const { t } = useTranslation()
  const stripe = useStripe()
  const elements = useElements()
  const qc = useQueryClient()
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!stripe || !elements) return
    setBusy(true)
    setMessage(null)
    const { error } = await stripe.confirmPayment({
      elements,
      redirect: 'if_required',
      confirmParams: { return_url: `${window.location.origin}/checkout/${checkoutId}` },
    })
    if (error) setMessage(error.message ?? t('checkout.failed'))
    await qc.invalidateQueries({ queryKey: ['checkout', checkoutId] })
    setBusy(false)
  }

  return (
    <form onSubmit={submit} className="card space-y-4 p-5">
      <PaymentElement />
      <TestHint>{t('checkout.stripeHint')}</TestHint>
      {message && <p role="alert" className="text-sm text-danger">{message}</p>}
      <button className="btn-primary w-full" disabled={!stripe || busy}>
        {busy ? t('checkout.processing') : t('checkout.pay', { total: formatMoney(total) })}
      </button>
    </form>
  )
}

// --- Mercado Pago -------------------------------------------------------------------------------

function MercadoPagoPayment({ checkout }: { checkout: Checkout }) {
  const { t } = useTranslation()
  const [tab, setTab] = useState<'pix' | 'card'>('pix')
  const key = checkout.provider_config.public_key
  if (key) initMp(key)
  const pix = checkout.attempts.find((a) => a.method === 'pix' && a.status === 'pending')
  const lastCard = [...checkout.attempts].reverse().find((a) => a.method === 'card')

  return (
    <div className="card space-y-4 p-5">
      <div role="tablist" className="flex rounded-xl bg-surface-2 p-1">
        {(['pix', 'card'] as const).map((value) => (
          <button key={value} role="tab" aria-selected={tab === value} onClick={() => setTab(value)}
            className={`min-h-9 flex-1 rounded-lg text-sm font-semibold ${tab === value ? 'bg-surface shadow-sm' : 'text-muted'}`}>
            {value === 'pix' ? 'Pix' : t('checkout.card')}
          </button>
        ))}
      </div>
      {tab === 'pix' ? <PixPanel checkoutId={checkout.id} attempt={pix} /> : <CardPanel checkout={checkout} last={lastCard} />}
    </div>
  )
}

function PixPanel({ checkoutId, attempt }: { checkoutId: string; attempt?: Attempt }) {
  const { t } = useTranslation()
  const start = useStartPayment(checkoutId)
  const [copied, setCopied] = useState(false)
  if (!attempt) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-muted">{t('checkout.pixText')}</p>
        <button className="btn-primary w-full" disabled={start.isPending} onClick={() => start.mutate({ method: 'pix' })}>
          {start.isPending ? t('checkout.generating') : t('checkout.generatePix')}
        </button>
        {start.isError && <p className="text-sm text-danger">{errorMessage(start.error, t)}</p>}
      </div>
    )
  }
  const { qr_code, qr_code_base64 } = attempt.client_data
  return (
    <div className="space-y-3">
      {qr_code_base64 && <img src={`data:image/png;base64,${qr_code_base64}`} alt={t('checkout.pixQr')} className="mx-auto h-56 w-56 rounded-xl bg-white p-2" />}
      {qr_code && (
        <div className="flex gap-2">
          <input readOnly value={qr_code} className="input font-mono text-xs" aria-label={t('checkout.pixCode')} />
          <button className="btn-secondary shrink-0" onClick={async () => { await navigator.clipboard.writeText(qr_code); setCopied(true) }}>
            {copied ? t('checkout.copied') : t('checkout.copy')}
          </button>
        </div>
      )}
      <p className="flex items-center gap-2 text-sm text-muted" aria-live="polite"><Spinner className="text-primary" />{t('checkout.waiting')}</p>
      <TestHint>{t('checkout.mpPixHint')}</TestHint>
    </div>
  )
}

function CardPanel({ checkout, last }: { checkout: Checkout; last?: Attempt }) {
  const { t } = useTranslation()
  const start = useStartPayment(checkout.id)
  return (
    <div className="space-y-3">
      {last?.status === 'failed' && <p role="alert" className="text-sm text-danger">{t('checkout.declined')}</p>}
      <CardPayment
        initialization={{ amount: checkout.total_cents / 100 }}
        customization={{ paymentMethods: { maxInstallments: checkout.provider_config.max_installments ?? 1 } }}
        onSubmit={async (form) => {
          await start.mutateAsync({
            method: 'card',
            card: {
              token: form.token,
              payment_method_id: form.payment_method_id,
              installments: form.installments,
              payer_email: form.payer.email,
              issuer_id: form.issuer_id ? String(form.issuer_id) : null,
            },
          })
        }}
      />
      {start.isError && <p className="text-sm text-danger">{errorMessage(start.error, t)}</p>}
      <TestHint>{t('checkout.mpCardHint')}</TestHint>
    </div>
  )
}

// --- Test provider (end-to-end tests and offline demos) -----------------------------------------

function Installments({ total, max, value, onChange }: { total: number; max: number; value: number; onChange: (n: number) => void }) {
  const { t } = useTranslation()
  return (
    <div>
      <label className="label" htmlFor="installments">{t('checkout.installments')}</label>
      <select id="installments" className="input" value={value} onChange={(e) => onChange(Number(e.target.value))}>
        {Array.from({ length: max }, (_, i) => i + 1).map((n) => (
          <option key={n} value={n}>{t('checkout.installmentOption', { count: n, amount: formatMoney(Math.round(total / n)) })}</option>
        ))}
      </select>
    </div>
  )
}

function FakePayment({ checkout }: { checkout: Checkout }) {
  const { t } = useTranslation()
  const start = useStartPayment(checkout.id)
  const [installments, setInstallments] = useState(1)
  return (
    <div className="card space-y-3 p-5">
      <p className="font-semibold">{t('checkout.fake.title')}</p>
      <p className="text-sm text-muted">{t('checkout.fake.text')}</p>
      <Installments total={checkout.total_cents} max={checkout.provider_config.max_installments ?? 1} value={installments} onChange={setInstallments} />
      <button className="btn-primary w-full" disabled={start.isPending}
        onClick={() => start.mutate({ method: 'card', card: { token: 'tok_approved', payment_method_id: 'visa', installments, payer_email: 'buyer@example.com' } })}>
        {t('checkout.fake.pay')}
      </button>
      {start.isError && <p className="text-sm text-danger">{errorMessage(start.error, t)}</p>}
    </div>
  )
}
