import { CardPayment, initMercadoPago } from '@mercadopago/sdk-react'
import { Elements, PaymentElement, useElements, useStripe } from '@stripe/react-stripe-js'
import { loadStripe, type Stripe } from '@stripe/stripe-js'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, json } from '../api/client'
import type { Attempt, Checkout } from '../api/orders'
import { formatMoney } from '../lib/format'

const stripePromises = new Map<string, Promise<Stripe | null>>()
function stripeFor(key: string) {
  if (!stripePromises.has(key)) stripePromises.set(key, loadStripe(key))
  return stripePromises.get(key)!
}

let mercadoPagoKey: string | null = null
function initMp(key: string) {
  if (mercadoPagoKey !== key) {
    initMercadoPago(key, { locale: 'pt-BR' })
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
  const minutes = Math.floor(left / 60000)
  const seconds = Math.floor((left % 60000) / 1000)
  return { left, label: `${minutes}:${String(seconds).padStart(2, '0')}` }
}

function useStartPayment(checkoutId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: unknown) =>
      api<Checkout>(`/api/checkouts/${checkoutId}/payments`, { method: 'POST', body: json(body) }),
    onSuccess: (data) => qc.setQueryData(['checkout', checkoutId], data),
  })
}

export function CheckoutPage() {
  const { id = '' } = useParams()
  const qc = useQueryClient()
  const checkout = useQuery({
    queryKey: ['checkout', id],
    queryFn: () => api<Checkout>(`/api/checkouts/${id}`),
    // While unpaid, poll: the payment is confirmed by the provider, not by this page.
    refetchInterval: (query) => (query.state.data?.status === 'pending' ? 3000 : false),
  })
  const cancel = useMutation({
    mutationFn: () => api<Checkout>(`/api/checkouts/${id}/cancel`, { method: 'POST' }),
    onSuccess: (data) => qc.setQueryData(['checkout', id], data),
  })

  if (checkout.isPending) return <p className="text-stone-500">Loading…</p>
  if (checkout.isError) return <p className="text-red-700">{checkout.error.message}</p>
  const data = checkout.data

  return (
    <div className="mx-auto grid max-w-5xl gap-6 lg:grid-cols-[1fr_340px]">
      <section className="space-y-4">
        {data.status === 'pending' && <PendingHeader checkout={data} />}
        {data.status === 'paid' && (
          <div className="card space-y-2 border-brand-500 bg-brand-50 p-6">
            <h1 className="text-xl font-semibold text-brand-800">Payment confirmed</h1>
            <p className="text-sm text-stone-700">
              The seller has been notified. You can follow the delivery in your orders.
            </p>
            <Link to="/orders" className="btn-primary">See my orders</Link>
          </div>
        )}
        {(data.status === 'expired' || data.status === 'cancelled') && (
          <div className="card space-y-2 p-6">
            <h1 className="text-xl font-semibold">
              {data.status === 'expired' ? 'This checkout expired' : 'Checkout cancelled'}
            </h1>
            <p className="text-sm text-stone-700">
              The items went back on sale. If a payment still arrives, it is refunded automatically.
            </p>
            <Link to="/" className="btn-secondary">Back to listings</Link>
          </div>
        )}
        {data.status === 'pending' && data.provider === 'stripe' && <StripePayment checkout={data} />}
        {data.status === 'pending' && data.provider === 'mercadopago' && <MercadoPagoPayment checkout={data} />}
        {data.status === 'pending' && (
          <button className="text-sm text-stone-600 hover:text-red-700" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
            Cancel and release the items
          </button>
        )}
      </section>
      <Summary checkout={data} />
    </div>
  )
}

function PendingHeader({ checkout }: { checkout: Checkout }) {
  const { left, label } = useCountdown(checkout.expires_at)
  return (
    <div className="flex items-center justify-between gap-4">
      <h1 className="text-xl font-semibold">Payment</h1>
      <span className={`rounded-full px-3 py-1 text-sm font-medium ${left < 120_000 ? 'bg-red-50 text-red-700' : 'bg-stone-100 text-stone-700'}`}>
        Reserved for {label}
      </span>
    </div>
  )
}

function Summary({ checkout }: { checkout: Checkout }) {
  return (
    <aside className="card h-fit divide-y divide-stone-100 text-sm">
      {checkout.orders.map((order) => (
        <div key={order.id} className="space-y-1 p-4">
          <p className="text-xs text-stone-500">Sold by {order.seller.display_name}</p>
          {order.items.map((item) => (
            <div key={item.listing_id} className="flex justify-between gap-2">
              <span>{item.quantity} × {item.title}</span>
              <span>{formatMoney(item.unit_price_cents * item.quantity)}</span>
            </div>
          ))}
          <div className="flex justify-between text-stone-500">
            <span>Shipping</span>
            <span>{formatMoney(order.shipping_cents)}</span>
          </div>
        </div>
      ))}
      <div className="flex justify-between p-4 text-base font-semibold">
        <span>Total</span>
        <span>{formatMoney(checkout.total_cents)}</span>
      </div>
    </aside>
  )
}

// --- Stripe ---------------------------------------------------------------------------------

function StripePayment({ checkout }: { checkout: Checkout }) {
  const start = useStartPayment(checkout.id)
  const attempt = checkout.attempts.find((a) => a.method === 'element' && a.status === 'pending')
  const secret = attempt?.client_data.client_secret
  const key = checkout.provider_config.publishable_key

  useEffect(() => {
    if (!attempt && !start.isPending && !start.isError) start.mutate({ method: 'element' })
  }, [attempt, start])

  if (start.isError) return <p className="text-red-700">{start.error.message}</p>
  if (!secret || !key) return <p className="text-stone-500">Preparing payment…</p>
  return (
    <Elements
      stripe={stripeFor(key)}
      options={{ clientSecret: secret, appearance: { theme: 'stripe', variables: { colorPrimary: '#0f766e' } } }}
    >
      <StripeForm checkoutId={checkout.id} total={checkout.total_cents} />
    </Elements>
  )
}

function StripeForm({ checkoutId, total }: { checkoutId: string; total: number }) {
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
    if (error) setMessage(error.message ?? 'The payment did not go through.')
    await qc.invalidateQueries({ queryKey: ['checkout', checkoutId] })
    setBusy(false)
  }

  return (
    <form onSubmit={submit} className="card space-y-4 p-5">
      <PaymentElement />
      <TestHint>
        Test card <code>4242 4242 4242 4242</code>, any future date and CVC. For Pix, choose Pix
        and confirm: Stripe shows a test QR code with an option to simulate the payment.
      </TestHint>
      {message && <p role="alert" className="text-sm text-red-700">{message}</p>}
      <button className="btn-primary w-full" disabled={!stripe || busy}>
        {busy ? 'Processing…' : `Pay ${formatMoney(total)}`}
      </button>
    </form>
  )
}

// --- Mercado Pago ---------------------------------------------------------------------------

function MercadoPagoPayment({ checkout }: { checkout: Checkout }) {
  const [tab, setTab] = useState<'pix' | 'card'>('pix')
  const key = checkout.provider_config.public_key
  if (key) initMp(key)
  const pix = checkout.attempts.find((a) => a.method === 'pix' && a.status === 'pending')
  const lastCard = [...checkout.attempts].reverse().find((a) => a.method === 'card')

  return (
    <div className="card p-5">
      <div role="tablist" className="mb-4 flex gap-2">
        {(['pix', 'card'] as const).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`rounded-full px-4 py-1.5 text-sm font-medium ${tab === t ? 'bg-brand-700 text-white' : 'bg-stone-100 text-stone-700'}`}
          >
            {t === 'pix' ? 'Pix' : 'Card'}
          </button>
        ))}
      </div>
      {tab === 'pix' ? <PixPanel checkoutId={checkout.id} attempt={pix} /> : <CardPanel checkout={checkout} last={lastCard} />}
    </div>
  )
}

function PixPanel({ checkoutId, attempt }: { checkoutId: string; attempt?: Attempt }) {
  const start = useStartPayment(checkoutId)
  const [copied, setCopied] = useState(false)
  if (!attempt) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-stone-600">Pay instantly from any bank app with a Pix QR code.</p>
        <button className="btn-primary" disabled={start.isPending} onClick={() => start.mutate({ method: 'pix' })}>
          {start.isPending ? 'Generating…' : 'Generate Pix code'}
        </button>
        {start.isError && <p className="text-sm text-red-700">{start.error.message}</p>}
      </div>
    )
  }
  const { qr_code, qr_code_base64 } = attempt.client_data
  return (
    <div className="space-y-3">
      {qr_code_base64 && (
        <img src={`data:image/png;base64,${qr_code_base64}`} alt="Pix QR code" className="mx-auto h-56 w-56" />
      )}
      {qr_code && (
        <div className="flex gap-2">
          <input readOnly value={qr_code} className="input font-mono text-xs" aria-label="Pix copy and paste code" />
          <button
            className="btn-secondary"
            onClick={async () => {
              await navigator.clipboard.writeText(qr_code)
              setCopied(true)
            }}
          >
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
      )}
      <p className="text-sm text-stone-600" aria-live="polite">Waiting for the payment… this page updates by itself.</p>
      <TestHint>In the Mercado Pago sandbox this Pix is approved automatically within a few seconds.</TestHint>
    </div>
  )
}

function CardPanel({ checkout, last }: { checkout: Checkout; last?: Attempt }) {
  const start = useStartPayment(checkout.id)
  return (
    <div className="space-y-3">
      {last?.status === 'failed' && (
        <p role="alert" className="text-sm text-red-700">
          The card was declined{last.status_detail ? ` (${last.status_detail})` : ''}. Try another card or Pix.
        </p>
      )}
      <CardPayment
        initialization={{ amount: checkout.total_cents / 100 }}
        customization={{ paymentMethods: { maxInstallments: 1 } }}
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
      {start.isError && <p className="text-sm text-red-700">{start.error.message}</p>}
      <TestHint>
        Mercado Pago test cards need the sandbox test buyer&apos;s email and the cardholder name <code>APRO</code>.
      </TestHint>
    </div>
  )
}

function TestHint({ children }: { children: React.ReactNode }) {
  return <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900">{children}</p>
}
