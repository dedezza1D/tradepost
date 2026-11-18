import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useMe } from '../api/auth'
import {
  useConversation,
  useInbox,
  useMarkRead,
  useMessages,
  useOfferActions,
  useSendMessage,
  type OfferAction,
} from '../api/chat'
import type { ChatMessage, Conversation, Offer } from '../api/types'
import { Avatar } from '../components/Avatar'
import { ReportSheet } from '../components/ReportSheet'
import { useToggleBlock } from '../api/social'
import { errorMessage } from '../lib/errors'
import { centsToInput, formatDateTime, formatMoney, parseMoney } from '../lib/format'
import { Icon } from '../ui/icons'
import { Badge, EmptyState, ErrorState, PageHeader, Sheet, Spinner } from '../ui/primitives'

const QUICK_REPLIES = ['available', 'condition', 'pickup', 'shipping'] as const

/** "offer:90000" style previews are rendered in the reader's language. */
function Preview({ text }: { text: string | null }) {
  const { t } = useTranslation()
  if (!text) return null
  const match = /^(offer|offer_accepted|offer_declined|offer_cancelled|offer_expired):(\d+)$/.exec(text)
  if (!match) return <>{text}</>
  return <>{t(`chat.preview.${match[1]}`, { amount: formatMoney(Number(match[2])) })}</>
}

function timeLabel(iso: string): string {
  const date = new Date(iso)
  const today = new Date()
  return date.toDateString() === today.toDateString()
    ? date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    : date.toLocaleDateString(undefined, { day: '2-digit', month: '2-digit' })
}

export function InboxPage() {
  const { t } = useTranslation()
  const inbox = useInbox()
  const items = inbox.data?.pages.flatMap((p) => p.items) ?? []
  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader title={t('chat.title')} />
      {inbox.isPending && <div className="space-y-2">{[0, 1, 2].map((i) => <div key={i} className="skeleton h-20" />)}</div>}
      {inbox.isError && <ErrorState message={errorMessage(inbox.error, t)} onRetry={() => void inbox.refetch()} />}
      {inbox.isSuccess && items.length === 0 && (
        <EmptyState icon="chat" title={t('chat.emptyTitle')} text={t('chat.emptyText')} action={{ label: t('feed.tabs.trending'), to: '/' }} />
      )}
      {items.length > 0 && (
        <ul className="card divide-y divide-line overflow-hidden">
          {items.map((c) => (
            <li key={c.id}>
              <Link to={`/inbox/${c.id}`} className="flex items-center gap-3 p-3 hover:bg-surface-2">
                <span className="relative shrink-0">
                  {c.listing.thumb_url ? (
                    <img src={c.listing.thumb_url} alt="" className="h-14 w-14 rounded-xl object-cover" />
                  ) : (
                    <span className="grid h-14 w-14 place-items-center rounded-xl bg-surface-2 text-muted"><Icon name="camera" /></span>
                  )}
                  <span className="absolute -right-1.5 -bottom-1.5 rounded-full ring-2 ring-surface">
                    <Avatar name={c.other.display_name} url={c.other.avatar_url} size="sm" />
                  </span>
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-baseline justify-between gap-2">
                    <span className={`truncate ${c.unread ? 'font-bold' : 'font-semibold'}`}>{c.other.display_name}</span>
                    <span className="shrink-0 text-xs text-muted">{timeLabel(c.last_message_at)}</span>
                  </span>
                  <span className="block truncate text-xs text-muted">
                    {t(c.role === 'buyer' ? 'chat.buying' : 'chat.selling')} · {c.listing.title}
                  </span>
                  <span className={`flex items-center gap-2 text-sm ${c.unread ? 'font-semibold text-fg' : 'text-muted'}`}>
                    <span className="min-w-0 flex-1 truncate"><Preview text={c.last_message_preview} /></span>
                    {c.unread > 0 && (
                      <span className="grid h-5 min-w-5 place-items-center rounded-full bg-primary px-1.5 text-[11px] font-bold text-primary-fg">
                        <span className="sr-only">{t('chat.unread', { count: c.unread })}</span>
                        <span aria-hidden>{c.unread}</span>
                      </span>
                    )}
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {inbox.hasNextPage && (
        <button className="btn-secondary mt-4 w-full" disabled={inbox.isFetchingNextPage} onClick={() => void inbox.fetchNextPage()}>
          {t('notifications.loadMore')}
        </button>
      )}
    </div>
  )
}

function AmountSheet({
  open,
  onClose,
  title,
  max,
  initial,
  onSubmit,
  pending,
  error,
}: {
  open: boolean
  onClose: () => void
  title: string
  max: number
  initial: number
  onSubmit: (cents: number) => void
  pending: boolean
  error: unknown
}) {
  const { t } = useTranslation()
  const [value, setValue] = useState(centsToInput(initial))
  const cents = parseMoney(value)
  const invalid = cents === null || cents <= 0 || cents > max
  function submit(e: FormEvent) {
    e.preventDefault()
    if (!invalid && cents !== null) onSubmit(cents)
  }
  return (
    <Sheet open={open} onClose={onClose} title={title}>
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className="label" htmlFor="offer-amount">{t('chat.offer.amount')}</label>
          <div className="relative">
            <span className="absolute top-1/2 left-3 -translate-y-1/2 text-muted">R$</span>
            <input id="offer-amount" inputMode="decimal" className="input pl-10 text-lg font-semibold" value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
          </div>
          <p className="mt-1 text-xs text-muted">{t('chat.offer.listed', { price: formatMoney(max) })}</p>
          {cents !== null && cents > max && <p className="mt-1 text-xs text-danger">{t('errors.offer_above_price')}</p>}
        </div>
        <div className="flex flex-wrap gap-2">
          {[0.9, 0.85, 0.8].map((f) => (
            <button key={f} type="button" className="rounded-full border border-line px-3 py-1.5 text-sm hover:border-primary" onClick={() => setValue(centsToInput(Math.round((max * f) / 100) * 100))}>
              {formatMoney(Math.round((max * f) / 100) * 100)}
            </button>
          ))}
        </div>
        <p className="text-xs text-muted">{t('chat.offer.hint')}</p>
        {error != null && <p role="alert" className="text-sm text-danger">{errorMessage(error, t)}</p>}
        <button className="btn-primary w-full" disabled={invalid || pending}>{t('chat.offer.send')}</button>
      </form>
    </Sheet>
  )
}

function OfferCard({
  message,
  offer,
  conversation,
  meId,
  onAction,
  busy,
}: {
  message: ChatMessage
  offer: Offer
  conversation: Conversation
  meId: string
  onAction: (offer: Offer, action: OfferAction) => void
  busy: boolean
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const mine = offer.proposer_id === meId
  const fromSeller = offer.proposer_id !== meId ? conversation.role === 'buyer' : conversation.role === 'seller'
  const tones = { pending: 'warning', accepted: 'success', declined: 'danger', countered: 'neutral', cancelled: 'neutral', expired: 'neutral' } as const
  const isBuyer = conversation.role === 'buyer'
  return (
    <div className={`flex ${message.sender_id === meId ? 'justify-end' : 'justify-start'}`}>
      <div className="card w-full max-w-xs space-y-3 p-4">
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs font-semibold tracking-wide text-muted uppercase">
            {fromSeller ? t('chat.offer.counterLabel') : t('chat.offer.label')}
          </span>
          <Badge tone={offer.paid ? 'success' : tones[offer.status]}>{offer.paid ? t('chat.offer.paid') : t(`chat.offer.status.${offer.status}`)}</Badge>
        </div>
        <p className="text-2xl font-extrabold tracking-tight">{formatMoney(offer.amount_cents)}</p>
        {offer.status === 'pending' && (
          <p className="text-xs text-muted">{t('chat.offer.expires', { time: formatDateTime(offer.expires_at) })}</p>
        )}
        {offer.status === 'pending' && !mine && (
          <div className="grid grid-cols-3 gap-2">
            <button className="btn-primary min-h-10 px-2 text-sm" disabled={busy} onClick={() => onAction(offer, 'accept')}>{t('chat.offer.accept')}</button>
            <button className="btn-secondary min-h-10 px-2 text-sm" disabled={busy} onClick={() => onAction(offer, 'counter')}>{t('chat.offer.counter')}</button>
            <button className="btn-ghost min-h-10 px-2 text-sm text-danger" disabled={busy} onClick={() => onAction(offer, 'decline')}>{t('chat.offer.decline')}</button>
          </div>
        )}
        {offer.status === 'pending' && mine && (
          <button className="btn-ghost min-h-9 w-full text-sm" disabled={busy} onClick={() => onAction(offer, 'cancel')}>{t('chat.offer.cancel')}</button>
        )}
        {offer.status === 'accepted' && isBuyer && !offer.paid && (
          <>
            <button
              className="btn-primary w-full"
              onClick={() =>
                navigate(
                  offer.checkout_id
                    ? `/checkout/${offer.checkout_id}`
                    : `/checkout?offer=${offer.id}&listing=${conversation.listing.id}&amount=${offer.amount_cents}&qty=${offer.quantity}`,
                )
              }
            >
              {t('chat.offer.pay', { amount: formatMoney(offer.amount_cents * offer.quantity) })}
            </button>
            {offer.pay_by && <p className="text-xs text-muted">{t('chat.offer.payBy', { time: formatDateTime(offer.pay_by) })}</p>}
          </>
        )}
        {offer.status === 'accepted' && !isBuyer && !offer.paid && <p className="text-xs text-muted">{t('chat.offer.waitingPayment')}</p>}
      </div>
    </div>
  )
}

function EventLine({ message, conversation, meId }: { message: ChatMessage; conversation: Conversation; meId: string }) {
  const { t } = useTranslation()
  const who = message.sender_id === meId ? t('chat.you') : message.sender_id ? conversation.other.display_name : ''
  return (
    <p className="text-center text-xs text-muted">
      {t(`chat.event.${message.kind}`, { who, amount: formatMoney(message.offer?.amount_cents ?? 0) })}
    </p>
  )
}

function Bubble({ message, mine, seen }: { message: ChatMessage; mine: boolean; seen: boolean }) {
  const { t } = useTranslation()
  return (
    <div className={`flex flex-col ${mine ? 'items-end' : 'items-start'}`}>
      <div className={`max-w-[80%] rounded-2xl px-3.5 py-2 text-[15px] leading-snug whitespace-pre-wrap break-words ${mine ? 'rounded-br-md bg-primary text-primary-fg' : 'rounded-bl-md bg-surface ring-1 ring-line'}`}>
        {message.body}
      </div>
      {message.warning && (
        <p role="note" className="mt-1 flex max-w-[80%] items-start gap-1.5 rounded-xl bg-warning-soft px-2.5 py-1.5 text-xs text-warning-fg">
          <Icon name="shield" size={14} className="mt-0.5 shrink-0" />
          {t(`chat.warning.${message.warning}`)}
        </p>
      )}
      <span className="mt-0.5 px-1 text-[11px] text-muted">
        {timeLabel(message.created_at)}
        {mine && seen && ` · ${t('chat.seen')}`}
      </span>
    </div>
  )
}

function ConversationMenu({ conversation }: { conversation: Conversation }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [reporting, setReporting] = useState(false)
  const block = useToggleBlock(conversation.other.id)
  return (
    <>
      <button type="button" className="btn-ghost min-h-10 px-2" aria-label={t('social.moreActions')} onClick={() => setOpen(true)}>
        <Icon name="more" />
      </button>
      <Sheet open={open} onClose={() => setOpen(false)} title={conversation.other.display_name}>
        <ul className="-mx-2 space-y-1">
          <li><Link to={`/users/${conversation.other.id}`} className="btn-ghost w-full justify-start"><Icon name="user" size={18} />{t('chat.viewProfile')}</Link></li>
          <li><Link to={`/listings/${conversation.listing.id}`} className="btn-ghost w-full justify-start"><Icon name="tag" size={18} />{t('chat.viewListing')}</Link></li>
          {!conversation.blocked && (
            <li>
              <button type="button" className="btn-ghost w-full justify-start" disabled={block.isPending}
                onClick={() => window.confirm(t('social.blockConfirm', { name: conversation.other.display_name })) && block.mutate(true, { onSuccess: () => setOpen(false) })}>
                <Icon name="ban" size={18} />{t('social.block')}
              </button>
            </li>
          )}
          <li>
            <button type="button" className="btn-ghost w-full justify-start text-danger" onClick={() => { setOpen(false); setReporting(true) }}>
              <Icon name="flag" size={18} />{t('chat.report')}
            </button>
          </li>
        </ul>
      </Sheet>
      <ReportSheet open={reporting} onClose={() => setReporting(false)} target={{ type: 'user', id: conversation.other.id }} />
    </>
  )
}

export function ConversationPage() {
  const { t } = useTranslation()
  const { id = '' } = useParams()
  const { data: me } = useMe()
  const conversation = useConversation(id)
  const messages = useMessages(id)
  const send = useSendMessage(id)
  const markRead = useMarkRead(id)
  const { make, respond } = useOfferActions(id)
  const [draft, setDraft] = useState('')
  const [params, setParams] = useSearchParams()
  // Arriving from "Make an offer" on the listing page opens the offer form.
  const [offering, setOffering] = useState(params.get('offer') === '1')
  const [countering, setCountering] = useState<Offer | null>(null)
  const bottom = useRef<HTMLDivElement>(null)

  const items = useMemo(() => [...(messages.data?.pages ?? [])].reverse().flatMap((p) => p.items), [messages.data])
  const lastId = items.at(-1)?.id
  const c = conversation.data

  // New messages: scroll to them and mark the conversation read.
  const { mutate: read } = markRead
  useEffect(() => {
    if (lastId === undefined) return
    bottom.current?.scrollIntoView({ block: 'end' })
    if (c && c.unread > 0) read()
  }, [lastId, c, read])

  if (conversation.isPending || messages.isPending) {
    return <div className="flex justify-center py-16 text-primary"><Spinner /></div>
  }
  if (conversation.isError) return <ErrorState message={errorMessage(conversation.error, t)} />
  if (!c || !me) return null

  const pendingOffer = items.map((m) => m.offer).find((o) => o?.status === 'pending')
  const canOffer = c.role === 'buyer' && !pendingOffer && c.listing.status === 'active' && !c.blocked
  const lastMine = [...items].reverse().find((m) => m.sender_id === me.id && m.kind === 'text')

  function submit(e: FormEvent) {
    e.preventDefault()
    const body = draft.trim()
    if (!body) return
    send.mutate(body, { onSuccess: () => setDraft('') })
  }
  function onAction(offer: Offer, action: OfferAction) {
    if (action === 'counter') setCountering(offer)
    else respond.mutate({ offerId: offer.id, action })
  }

  return (
    <div className="mx-auto max-w-2xl pb-40 md:pb-28">
      <header className="sticky top-14 z-10 -mx-4 space-y-2 border-b border-line bg-bg/95 px-4 pt-1 pb-3 backdrop-blur md:top-16">
        <div className="flex items-center gap-2">
          <Link to="/inbox" className="btn-ghost -ml-2 min-h-10 px-2" aria-label={t('common.back')}><Icon name="chevronLeft" /></Link>
          <Link to={`/users/${c.other.id}`} className="flex min-w-0 flex-1 items-center gap-2">
            <Avatar name={c.other.display_name} url={c.other.avatar_url} size="sm" />
            <h1 className="truncate font-bold">{c.other.display_name}</h1>
          </Link>
          <ConversationMenu conversation={c} />
        </div>
        <Link to={`/listings/${c.listing.id}`} className="flex items-center gap-3 rounded-2xl bg-surface p-2 ring-1 ring-line">
          {c.listing.thumb_url ? <img src={c.listing.thumb_url} alt="" className="h-11 w-11 rounded-lg object-cover" /> : <span className="h-11 w-11 rounded-lg bg-surface-2" />}
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-semibold">{c.listing.title}</span>
            <span className="text-sm">{formatMoney(c.listing.price_cents)}</span>
          </span>
          {c.listing.status !== 'active' && <Badge>{t(`listing.status.${c.listing.status}`)}</Badge>}
        </Link>
      </header>

      <p className="my-3 flex items-start gap-2 rounded-2xl bg-primary-soft px-3 py-2 text-xs text-primary">
        <Icon name="shield" size={16} className="shrink-0" />
        {t('chat.safety')}
      </p>

      {messages.hasNextPage && (
        <button className="btn-ghost mx-auto mb-2 flex text-sm" disabled={messages.isFetchingNextPage} onClick={() => void messages.fetchNextPage()}>
          {t('chat.older')}
        </button>
      )}

      <ol className="space-y-3" aria-live="polite">
        {items.map((m) => (
          <li key={m.id}>
            {m.kind === 'text' ? (
              <Bubble message={m} mine={m.sender_id === me.id} seen={m.id === lastMine?.id && Boolean(c.other_read_at && new Date(c.other_read_at) >= new Date(m.created_at))} />
            ) : m.kind === 'offer' && m.offer ? (
              <OfferCard message={m} offer={m.offer} conversation={c} meId={me.id} onAction={onAction} busy={respond.isPending} />
            ) : (
              <EventLine message={m} conversation={c} meId={me.id} />
            )}
          </li>
        ))}
      </ol>
      {respond.isError && <p role="alert" className="mt-2 text-center text-sm text-danger">{errorMessage(respond.error, t)}</p>}
      <div ref={bottom} />

      <div className="fixed inset-x-0 bottom-[calc(57px+env(safe-area-inset-bottom))] z-20 border-t border-line bg-surface/95 px-4 py-2.5 backdrop-blur md:bottom-0">
        <div className="mx-auto max-w-2xl space-y-2">
          {c.blocked ? (
            <p className="py-2 text-center text-sm text-muted">{t('chat.blocked')}</p>
          ) : (
            <>
              {items.length === 0 && c.role === 'buyer' && (
                <div className="-mx-4 flex gap-2 overflow-x-auto px-4 no-scrollbar">
                  {QUICK_REPLIES.map((q) => (
                    <button key={q} type="button" className="shrink-0 rounded-full border border-line bg-surface px-3 py-1.5 text-sm hover:border-primary"
                      onClick={() => send.mutate(t(`chat.quick.${q}`))}>
                      {t(`chat.quick.${q}`)}
                    </button>
                  ))}
                </div>
              )}
              <form onSubmit={submit} className="flex items-end gap-2">
                {canOffer && (
                  <button type="button" className="btn-secondary min-h-11 shrink-0 px-3" aria-label={t('chat.offer.make')} onClick={() => setOffering(true)}>
                    <Icon name="tag" size={18} />
                    <span className="hidden sm:inline">{t('chat.offer.make')}</span>
                  </button>
                )}
                <label htmlFor="chat-input" className="sr-only">{t('chat.inputLabel')}</label>
                <textarea id="chat-input" rows={1} maxLength={1000} value={draft} placeholder={t('chat.placeholder')}
                  className="input max-h-32 min-h-11 flex-1 resize-none py-2.5"
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      submit(e)
                    }
                  }} />
                <button className="btn-primary min-h-11 shrink-0 px-4" disabled={!draft.trim() || send.isPending} aria-label={t('chat.send')}>
                  <Icon name="send" size={18} />
                </button>
              </form>
              {send.isError && <p role="alert" className="text-sm text-danger">{errorMessage(send.error, t)}</p>}
            </>
          )}
        </div>
      </div>

      {offering && canOffer && (
        <AmountSheet open onClose={() => { setOffering(false); setParams({}, { replace: true }) }} title={t('chat.offer.make')} max={c.listing.price_cents}
          initial={Math.round((c.listing.price_cents * 0.9) / 100) * 100} pending={make.isPending} error={make.error}
          onSubmit={(cents) => make.mutate(cents, { onSuccess: () => { setOffering(false); setParams({}, { replace: true }) } })} />
      )}
      {countering && (
        <AmountSheet open onClose={() => setCountering(null)} title={t('chat.offer.counterTitle')} max={c.listing.price_cents}
          initial={Math.round((countering.amount_cents + c.listing.price_cents) / 200) * 100} pending={respond.isPending} error={respond.error}
          onSubmit={(cents) => respond.mutate({ offerId: countering.id, action: 'counter', amount_cents: cents }, { onSuccess: () => setCountering(null) })} />
      )}
    </div>
  )
}
