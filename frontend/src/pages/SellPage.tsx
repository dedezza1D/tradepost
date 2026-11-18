import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useParams } from 'react-router-dom'
import { useMe } from '../api/auth'
import { useCategories } from '../api/catalog'
import { ApiError, api, json } from '../api/client'
import type { Condition, Listing, ListingInput, Photo } from '../api/types'
import { CityPicker, type City } from '../components/CityPicker'
import { errorMessage } from '../lib/errors'
import { centsToInput, formatMoney, parseMoney } from '../lib/format'
import { Icon } from '../ui/icons'
import { Badge, ErrorState, PageHeader, Spinner } from '../ui/primitives'

const MAX_PHOTOS = 8
const MAX_BYTES = 5 * 1024 * 1024

interface Details {
  title: string
  description: string
  category_slug: string
  condition: Condition
  price: string
  shipping: string
  stock: string
  city: City | null
}

const EMPTY: Details = { title: '', description: '', category_slug: '', condition: 'used', price: '', shipping: '0', stock: '1', city: null }

function fromListing(l: Listing): Details {
  return {
    title: l.title,
    description: l.description,
    category_slug: l.category_slug,
    condition: l.condition,
    price: centsToInput(l.price_cents),
    shipping: centsToInput(l.shipping_cents),
    stock: String(l.stock),
    city: { ibge_code: l.municipality_id ?? 0, name: l.city, state: l.state },
  }
}

/** Returns the API payload, or the translation key of the first problem. */
function toInput(d: Details): ListingInput | string {
  const price = parseMoney(d.price)
  const shipping = parseMoney(d.shipping || '0')
  const stock = Number(d.stock)
  if (!price || price <= 0) return 'sell.errors.price'
  if (shipping === null) return 'sell.errors.shipping'
  if (!Number.isInteger(stock) || stock < 0) return 'sell.errors.stock'
  if (!d.city) return 'sell.errors.city'
  return {
    title: d.title.trim(),
    description: d.description.trim(),
    category_slug: d.category_slug,
    condition: d.condition,
    price_cents: price,
    shipping_cents: shipping,
    stock,
    city: d.city.name,
    state: d.city.state,
  }
}

// --- photos ---------------------------------------------------------------------------------

interface Tile {
  key: string
  src: string
}

/** Photo strip with drag-and-drop (desktop) and move buttons (touch, keyboard, screen readers). */
function PhotoStrip({ tiles, onMove, onRemove, onAdd, busy }: {
  tiles: Tile[]
  onMove: (from: number, to: number) => void
  onRemove: (index: number) => void
  onAdd: (files: File[]) => void
  busy?: boolean
}) {
  const { t } = useTranslation()
  const dragged = useRef<number | null>(null)
  const input = useRef<HTMLInputElement>(null)
  return (
    <div className="space-y-3">
      <ul className="grid grid-cols-3 gap-2 sm:grid-cols-4">
        {tiles.map((tile, i) => (
          <li
            key={tile.key}
            draggable
            onDragStart={() => (dragged.current = i)}
            onDragOver={(e) => e.preventDefault()}
            onDrop={() => {
              if (dragged.current !== null && dragged.current !== i) onMove(dragged.current, i)
              dragged.current = null
            }}
            className="group relative aspect-square overflow-hidden rounded-xl bg-surface-2 ring-1 ring-line"
          >
            <img src={tile.src} alt={t('sell.photoN', { n: i + 1 })} className="h-full w-full object-cover" />
            {i === 0 && <span className="absolute top-1.5 left-1.5"><Badge tone="primary">{t('sell.cover')}</Badge></span>}
            <div className="absolute inset-x-1 bottom-1 flex justify-between">
              <button type="button" className="rounded-full bg-surface/90 p-1.5 shadow disabled:opacity-0" disabled={i === 0 || busy}
                onClick={() => onMove(i, i - 1)} aria-label={t('sell.moveLeft', { n: i + 1 })}>
                <Icon name="chevronLeft" size={16} />
              </button>
              <button type="button" className="rounded-full bg-surface/90 p-1.5 text-danger shadow" disabled={busy}
                onClick={() => onRemove(i)} aria-label={t('sell.removePhoto', { n: i + 1 })}>
                <Icon name="trash" size={16} />
              </button>
              <button type="button" className="rounded-full bg-surface/90 p-1.5 shadow disabled:opacity-0" disabled={i === tiles.length - 1 || busy}
                onClick={() => onMove(i, i + 1)} aria-label={t('sell.moveRight', { n: i + 1 })}>
                <Icon name="chevronRight" size={16} />
              </button>
            </div>
          </li>
        ))}
        {tiles.length < MAX_PHOTOS && (
          <li>
            <button type="button" onClick={() => input.current?.click()} disabled={busy}
              className="flex aspect-square w-full flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed border-line text-sm font-medium text-muted hover:border-primary hover:text-primary">
              {busy ? <Spinner /> : <Icon name="camera" size={26} />}
              {t('sell.addPhotos')}
            </button>
          </li>
        )}
      </ul>
      <input ref={input} type="file" accept="image/jpeg,image/png,image/webp" multiple className="sr-only" tabIndex={-1}
        onChange={(e) => {
          onAdd(Array.from(e.target.files ?? []).slice(0, MAX_PHOTOS - tiles.length))
          e.target.value = ''
        }} />
      <p className="text-xs text-muted">{t('sell.photoHint', { count: tiles.length, max: MAX_PHOTOS })}</p>
    </div>
  )
}

function move<T>(items: T[], from: number, to: number): T[] {
  const next = [...items]
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}

// --- fields ---------------------------------------------------------------------------------

function DetailFields({ d, set }: { d: Details; set: (patch: Partial<Details>) => void }) {
  const { t } = useTranslation()
  const { data: categories } = useCategories()
  return (
    <div className="space-y-4">
      <div>
        <label className="label" htmlFor="title">{t('sell.title')}</label>
        <input id="title" className="input" required minLength={5} maxLength={120} value={d.title} onChange={(e) => set({ title: e.target.value })}
          placeholder={t('sell.titlePlaceholder')} />
        <p className="mt-1 text-right text-xs text-muted">{d.title.length}/120</p>
      </div>
      <div>
        <label className="label" htmlFor="description">{t('sell.description')}</label>
        <textarea id="description" className="input min-h-32" required minLength={10} maxLength={5000} value={d.description}
          onChange={(e) => set({ description: e.target.value })} placeholder={t('sell.descriptionPlaceholder')} />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className="label" htmlFor="category">{t('search.category')}</label>
          <select id="category" className="input" required value={d.category_slug} onChange={(e) => set({ category_slug: e.target.value })}>
            <option value="">{t('sell.choose')}</option>
            {categories?.map((c) => <option key={c.slug} value={c.slug}>{t(`categories.${c.slug}`, { defaultValue: c.name })}</option>)}
          </select>
        </div>
        <fieldset>
          <legend className="label">{t('search.condition')}</legend>
          <div className="flex gap-2">
            {(['new', 'used'] as const).map((c) => (
              <label key={c} className="flex-1">
                <input type="radio" name="condition" className="peer sr-only" checked={d.condition === c} onChange={() => set({ condition: c })} />
                <span className="flex min-h-11 cursor-pointer items-center justify-center rounded-xl border border-line text-sm font-medium peer-checked:border-primary peer-checked:bg-primary-soft peer-checked:text-primary peer-focus-visible:ring-2 peer-focus-visible:ring-primary">
                  {t(`listing.condition.${c}`)}
                </span>
              </label>
            ))}
          </div>
        </fieldset>
      </div>
    </div>
  )
}

function PriceFields({ d, set }: { d: Details; set: (patch: Partial<Details>) => void }) {
  const { t } = useTranslation()
  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <div>
          <label className="label" htmlFor="price">{t('sell.price')}</label>
          <input id="price" className="input" inputMode="decimal" required placeholder="0,00" value={d.price} onChange={(e) => set({ price: e.target.value })} />
        </div>
        <div>
          <label className="label" htmlFor="shipping">{t('sell.shipping')}</label>
          <input id="shipping" className="input" inputMode="decimal" value={d.shipping} onChange={(e) => set({ shipping: e.target.value })} />
          <p className="mt-1 text-xs text-muted">{t('sell.shippingHint')}</p>
        </div>
        <div>
          <label className="label" htmlFor="stock">{t('sell.stock')}</label>
          <input id="stock" className="input" type="number" min={0} max={10000} value={d.stock} onChange={(e) => set({ stock: e.target.value })} />
        </div>
      </div>
      <CityPicker required value={d.city} onChange={(city) => set({ city })} />
    </div>
  )
}

// --- new listing: steps -----------------------------------------------------------------------

interface LocalPhoto {
  key: string
  file: File
  src: string
}

function NewListing() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data: me } = useMe()
  const [step, setStep] = useState(0)
  const [photos, setPhotos] = useState<LocalPhoto[]>([])
  const [d, setD] = useState<Details>(EMPTY)
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState<string | null>(null)
  const set = (patch: Partial<Details>) => setD((prev) => ({ ...prev, ...patch }))

  useEffect(() => {
    if (me?.city && me.state && !d.city) set({ city: { ibge_code: me.municipality_id ?? 0, name: me.city, state: me.state } })
  }, [me, d.city])
  // Preview URLs are released when the page closes (not on every change: they are still shown).
  const latest = useRef(photos)
  latest.current = photos
  useEffect(() => () => latest.current.forEach((p) => URL.revokeObjectURL(p.src)), [])

  const steps = [t('sell.steps.photos'), t('sell.steps.details'), t('sell.steps.price'), t('sell.steps.review')]

  function addFiles(files: File[]) {
    const tooBig = files.filter((f) => f.size > MAX_BYTES)
    setError(tooBig.length ? t('errors.invalid_image') : null)
    setPhotos((prev) => [...prev, ...files.filter((f) => f.size <= MAX_BYTES).map((file) => ({ key: crypto.randomUUID(), file, src: URL.createObjectURL(file) }))])
  }

  function next(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (step === 0 && photos.length === 0) return setError(t('errors.photo_required'))
    if (step === 2) {
      const input = toInput(d)
      if (typeof input === 'string') return setError(t(input))
    }
    setStep((s) => Math.min(s + 1, steps.length - 1))
  }

  async function submit(publish: boolean) {
    const input = toInput(d)
    if (typeof input === 'string') return setError(t(input))
    setError(null)
    let created: Listing | null = null
    try {
      setProgress(t('sell.progress.creating'))
      created = await api<Listing>('/api/listings', { method: 'POST', body: json(input) })
      for (const [i, photo] of photos.entries()) {
        setProgress(t('sell.progress.uploading', { n: i + 1, total: photos.length }))
        const body = new FormData()
        body.append('file', photo.file)
        await api<Photo>(`/api/listings/${created.id}/photos`, { method: 'POST', body })
      }
      if (publish) {
        setProgress(t('sell.progress.publishing'))
        await api<Listing>(`/api/listings/${created.id}/publish`, { method: 'POST' })
      }
      void qc.invalidateQueries({ queryKey: ['my-listings'] })
      void qc.invalidateQueries({ queryKey: ['listings'] })
      navigate(publish ? `/listings/${created.id}` : `/sell/${created.id}`, { replace: true })
    } catch (err) {
      setProgress(null)
      setError(errorMessage(err, t))
      // The draft exists: continue in the editor, where nothing typed is lost.
      if (created) navigate(`/sell/${created.id}`, { replace: true })
    }
  }

  const input = toInput(d)
  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader title={t('sell.newTitle')} />
      <ol className="mb-5 grid grid-cols-4 gap-2" aria-label={t('sell.stepsLabel')}>
        {steps.map((label, i) => (
          <li key={label} aria-current={i === step ? 'step' : undefined}>
            <div className={`h-1.5 rounded-full ${i <= step ? 'bg-primary' : 'bg-surface-2'}`} />
            <span className={`mt-1.5 block truncate text-xs font-medium ${i === step ? 'text-fg' : 'text-muted'}`}>{label}</span>
          </li>
        ))}
      </ol>
      <form onSubmit={next} className="card space-y-5 p-5">
        {step === 0 && (
          <>
            <div>
              <h2 className="text-lg font-semibold">{t('sell.photosTitle')}</h2>
              <p className="text-sm text-muted">{t('sell.photosText')}</p>
            </div>
            <PhotoStrip tiles={photos} onAdd={addFiles} onMove={(a, b) => setPhotos((p) => move(p, a, b))}
              onRemove={(i) => setPhotos((p) => p.filter((_, j) => j !== i))} />
          </>
        )}
        {step === 1 && <DetailFields d={d} set={set} />}
        {step === 2 && <PriceFields d={d} set={set} />}
        {step === 3 && typeof input !== 'string' && (
          <div className="space-y-4">
            {photos[0] && <img src={photos[0].src} alt="" className="aspect-[4/3] w-full rounded-2xl object-cover" />}
            <div>
              <p className="text-2xl font-extrabold">{formatMoney(input.price_cents)}</p>
              <p className="font-semibold">{input.title}</p>
              <p className="text-sm text-muted">
                {t(`listing.condition.${input.condition}`)} · {input.city} - {input.state} · {t('listing.available', { count: input.stock })}
              </p>
              <p className="text-sm text-muted">
                {input.shipping_cents ? t('listing.shipping', { price: formatMoney(input.shipping_cents) }) : t('listing.freeShipping')}
              </p>
            </div>
            <p className="text-sm leading-relaxed whitespace-pre-line">{input.description}</p>
          </div>
        )}
        {error && <p role="alert" className="text-sm text-danger">{error}</p>}
        {progress && <p className="flex items-center gap-2 text-sm text-muted" aria-live="polite"><Spinner className="text-primary" />{progress}</p>}
        <div className="flex gap-2">
          {step > 0 && (
            <button type="button" className="btn-secondary" disabled={Boolean(progress)} onClick={() => { setError(null); setStep((s) => s - 1) }}>
              {t('common.back')}
            </button>
          )}
          {step < steps.length - 1 ? (
            <button className="btn-primary flex-1">{t('common.continue')}</button>
          ) : (
            <>
              <button type="button" className="btn-secondary" disabled={Boolean(progress)} onClick={() => void submit(false)}>{t('sell.saveDraft')}</button>
              <button type="button" className="btn-primary flex-1" disabled={Boolean(progress)} onClick={() => void submit(true)}>{t('sell.publish')}</button>
            </>
          )}
        </div>
      </form>
    </div>
  )
}

// --- editing an existing listing ----------------------------------------------------------------

function EditListing({ id }: { id: string }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const listing = useQuery({ queryKey: ['listing', id], queryFn: () => api<Listing>(`/api/listings/${id}`) })
  const [d, setD] = useState<Details>(EMPTY)
  const [saved, setSaved] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const set = (patch: Partial<Details>) => setD((prev) => ({ ...prev, ...patch }))

  useEffect(() => {
    if (listing.data) setD(fromListing(listing.data))
  }, [listing.data])

  const onListing = (l: Listing) => {
    qc.setQueryData(['listing', l.id], l)
    void qc.invalidateQueries({ queryKey: ['my-listings'] })
    void qc.invalidateQueries({ queryKey: ['listings'] })
  }
  const save = useMutation({
    mutationFn: (input: ListingInput) => api<Listing>(`/api/listings/${id}`, { method: 'PATCH', body: json({ ...input, version: listing.data?.version }) }),
    onSuccess: (l) => { onListing(l); setSaved(true) },
  })
  const action = useMutation({
    mutationFn: (kind: 'publish' | 'pause') => api<Listing>(`/api/listings/${id}/${kind}`, { method: 'POST' }),
    onSuccess: onListing,
  })
  const remove = useMutation({
    mutationFn: () => api<void>(`/api/listings/${id}`, { method: 'DELETE' }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['my-listings'] }); navigate('/me/listings') },
  })
  const photos = useMutation({
    mutationFn: async (op: { kind: 'add'; files: File[] } | { kind: 'remove'; photoId: string } | { kind: 'order'; ids: string[] }) => {
      if (op.kind === 'add') {
        for (const file of op.files) {
          const body = new FormData()
          body.append('file', file)
          await api<Photo>(`/api/listings/${id}/photos`, { method: 'POST', body })
        }
        return null
      }
      if (op.kind === 'remove') {
        await api<void>(`/api/listings/${id}/photos/${op.photoId}`, { method: 'DELETE' })
        return null
      }
      return api<Listing>(`/api/listings/${id}/photos/order`, { method: 'PUT', body: json({ photo_ids: op.ids }) })
    },
    onSettled: () => void listing.refetch(),
  })

  if (listing.isPending) return <div className="mx-auto max-w-2xl space-y-3"><div className="skeleton h-40" /><div className="skeleton h-64" /></div>
  if (listing.isError) return <ErrorState message={errorMessage(listing.error, t)} onRetry={() => void listing.refetch()} />

  const l = listing.data
  const current = l.photos
  const saveError = save.error instanceof ApiError && save.error.code === 'stale_version' ? t('errors.stale_version') : save.error ? errorMessage(save.error, t) : null

  function submit(event: FormEvent) {
    event.preventDefault()
    setSaved(false)
    const input = toInput(d)
    if (typeof input === 'string') return setFormError(t(input))
    setFormError(null)
    save.mutate(input)
  }

  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <PageHeader title={t('sell.editTitle')} back="/me/listings" action={<Badge tone={l.status === 'active' ? 'success' : 'neutral'}>{t(`listing.status.${l.status}`)}</Badge>} />

      <section className="card space-y-3 p-5">
        <h2 className="font-semibold">{t('sell.steps.photos')}</h2>
        <PhotoStrip
          tiles={current.map((p) => ({ key: p.id, src: p.thumb_url }))}
          busy={photos.isPending}
          onAdd={(files) => photos.mutate({ kind: 'add', files: files.filter((f) => f.size <= MAX_BYTES) })}
          onRemove={(i) => photos.mutate({ kind: 'remove', photoId: current[i].id })}
          onMove={(a, b) => photos.mutate({ kind: 'order', ids: move(current, a, b).map((p) => p.id) })}
        />
        {photos.isError && <p role="alert" className="text-sm text-danger">{errorMessage(photos.error, t)}</p>}
      </section>

      <form onSubmit={submit} className="card space-y-5 p-5">
        <DetailFields d={d} set={set} />
        <PriceFields d={d} set={set} />
        {(formError || saveError) && <p role="alert" className="text-sm text-danger">{formError ?? saveError}</p>}
        {saved && !save.isPending && <p className="text-sm text-success">{t('common.saved')}</p>}
        <button className="btn-primary w-full" disabled={save.isPending}>{save.isPending ? t('common.saving') : t('common.save')}</button>
      </form>

      <section className="card space-y-3 p-5">
        <h2 className="font-semibold">{t('sell.visibility')}</h2>
        {l.status !== 'active' && l.status !== 'sold_out' ? (
          <button className="btn-primary w-full" disabled={action.isPending} onClick={() => action.mutate('publish')}>{t('sell.publish')}</button>
        ) : (
          <button className="btn-secondary w-full" disabled={action.isPending} onClick={() => action.mutate('pause')}>{t('sell.pause')}</button>
        )}
        <button className="btn-danger w-full" disabled={remove.isPending} onClick={() => { if (confirm(t('sell.removeConfirm'))) remove.mutate() }}>
          {t('sell.remove')}
        </button>
        {(action.error ?? remove.error) && <p role="alert" className="text-sm text-danger">{errorMessage(action.error ?? remove.error, t)}</p>}
      </section>
    </div>
  )
}

export function SellPage() {
  const { id } = useParams()
  return id ? <EditListing id={id} /> : <NewListing />
}
