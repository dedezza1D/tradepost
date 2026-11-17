import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useCategories } from '../api/catalog'
import { ApiError, api, json } from '../api/client'
import { BR_STATES, type Condition, type Listing, type ListingInput, type Photo } from '../api/types'
import { useMe } from '../api/auth'
import { centsToInput, parseMoney, statusLabel } from '../lib/format'

interface FormState {
  title: string
  description: string
  category_slug: string
  condition: Condition
  price: string
  shipping: string
  stock: string
  city: string
  state: string
}

const EMPTY: FormState = {
  title: '',
  description: '',
  category_slug: '',
  condition: 'used',
  price: '',
  shipping: '0',
  stock: '1',
  city: '',
  state: '',
}

function fromListing(l: Listing): FormState {
  return {
    title: l.title,
    description: l.description,
    category_slug: l.category_slug,
    condition: l.condition,
    price: centsToInput(l.price_cents),
    shipping: centsToInput(l.shipping_cents),
    stock: String(l.stock),
    city: l.city,
    state: l.state,
  }
}

function toInput(f: FormState): ListingInput | string {
  const price = parseMoney(f.price)
  const shipping = parseMoney(f.shipping || '0')
  const stock = Number(f.stock)
  if (!price || price <= 0) return 'Enter a valid price.'
  if (shipping === null) return 'Enter a valid shipping cost.'
  if (!Number.isInteger(stock) || stock < 0) return 'Stock must be a whole number.'
  return {
    title: f.title,
    description: f.description,
    category_slug: f.category_slug,
    condition: f.condition,
    price_cents: price,
    shipping_cents: shipping,
    stock,
    city: f.city,
    state: f.state,
  }
}

export function SellPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data: me } = useMe()
  const { data: categories } = useCategories()
  const [form, setForm] = useState<FormState>(EMPTY)
  const [formError, setFormError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const listing = useQuery({
    queryKey: ['listing', id],
    queryFn: () => api<Listing>(`/api/listings/${id}`),
    enabled: Boolean(id),
  })

  useEffect(() => {
    if (listing.data) setForm(fromListing(listing.data))
    else if (!id && me) setForm((f) => ({ ...f, city: me.city ?? '', state: me.state ?? '' }))
  }, [listing.data, id, me])

  const onListing = (l: Listing) => {
    qc.setQueryData(['listing', l.id], l)
    qc.invalidateQueries({ queryKey: ['my-listings'] })
    qc.invalidateQueries({ queryKey: ['listings'] })
  }

  const save = useMutation({
    mutationFn: (input: ListingInput) =>
      id
        ? api<Listing>(`/api/listings/${id}`, {
            method: 'PATCH',
            body: json({ ...input, version: listing.data?.version }),
          })
        : api<Listing>('/api/listings', { method: 'POST', body: json(input) }),
    onSuccess: (l) => {
      onListing(l)
      setSaved(true)
      if (!id) navigate(`/sell/${l.id}`, { replace: true })
    },
  })

  const action = useMutation({
    mutationFn: (kind: 'publish' | 'pause') =>
      api<Listing>(`/api/listings/${id}/${kind}`, { method: 'POST' }),
    onSuccess: onListing,
  })

  const remove = useMutation({
    mutationFn: () => api<void>(`/api/listings/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['my-listings'] })
      navigate('/me/listings')
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    setSaved(false)
    const input = toInput(form)
    if (typeof input === 'string') {
      setFormError(input)
      return
    }
    setFormError(null)
    save.mutate(input)
  }

  const set = (key: keyof FormState) => (e: { target: { value: string } }) =>
    setForm({ ...form, [key]: e.target.value })

  if (id && listing.isPending) return <p className="text-stone-500">Loading…</p>
  if (id && listing.isError) return <p className="text-red-700">{listing.error.message}</p>

  const current = listing.data
  const saveError = save.error instanceof ApiError ? save.error : null
  const actionError = action.error ?? remove.error

  return (
    <div className="mx-auto grid max-w-5xl gap-6 lg:grid-cols-[1fr_320px]">
      <form onSubmit={submit} className="card space-y-4 p-6">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold">{id ? 'Edit listing' : 'New listing'}</h1>
          {current && (
            <span className="rounded-full bg-stone-100 px-3 py-1 text-xs font-medium">
              {statusLabel[current.status]}
            </span>
          )}
        </div>
        <div>
          <label className="label" htmlFor="title">Title</label>
          <input id="title" className="input" required minLength={5} maxLength={120}
            value={form.title} onChange={set('title')} />
        </div>
        <div>
          <label className="label" htmlFor="description">Description</label>
          <textarea id="description" className="input min-h-32" required minLength={10}
            maxLength={5000} value={form.description} onChange={set('description')} />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="category">Category</label>
            <select id="category" className="input" required value={form.category_slug}
              onChange={set('category_slug')}>
              <option value="">Choose…</option>
              {categories?.map((c) => (
                <option key={c.slug} value={c.slug}>{c.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="label" htmlFor="condition">Condition</label>
            <select id="condition" className="input" value={form.condition}
              onChange={set('condition')}>
              <option value="new">New</option>
              <option value="used">Used</option>
            </select>
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          <div>
            <label className="label" htmlFor="price">Price (R$)</label>
            <input id="price" className="input" inputMode="decimal" required placeholder="0,00"
              value={form.price} onChange={set('price')} />
          </div>
          <div>
            <label className="label" htmlFor="shipping">Shipping (R$)</label>
            <input id="shipping" className="input" inputMode="decimal" value={form.shipping}
              onChange={set('shipping')} />
          </div>
          <div>
            <label className="label" htmlFor="stock">Quantity</label>
            <input id="stock" className="input" type="number" min={0} max={10000}
              value={form.stock} onChange={set('stock')} />
          </div>
        </div>
        <div className="grid grid-cols-[1fr_96px] gap-4">
          <div>
            <label className="label" htmlFor="city">City</label>
            <input id="city" className="input" required minLength={2} maxLength={80}
              value={form.city} onChange={set('city')} />
          </div>
          <div>
            <label className="label" htmlFor="state">State</label>
            <select id="state" className="input" required value={form.state}
              onChange={set('state')}>
              <option value="">—</option>
              {BR_STATES.map((s) => (<option key={s}>{s}</option>))}
            </select>
          </div>
        </div>
        {(formError || saveError) && (
          <p role="alert" className="text-sm text-red-700">
            {formError ??
              (saveError?.code === 'stale_version'
                ? 'This listing changed in another tab. Reload the page to see the latest version.'
                : saveError?.message)}
          </p>
        )}
        {saved && !save.isPending && <p className="text-sm text-brand-700">Saved.</p>}
        <button className="btn-primary" disabled={save.isPending}>
          {save.isPending ? 'Saving…' : id ? 'Save changes' : 'Save draft and add photos'}
        </button>
      </form>

      {current && (
        <div className="space-y-4">
          <PhotoManager listing={current} onChange={() => listing.refetch()} />
          <div className="card space-y-2 p-5">
            <h2 className="font-semibold">Visibility</h2>
            {current.status !== 'active' && current.status !== 'sold_out' ? (
              <button className="btn-primary w-full" disabled={action.isPending}
                onClick={() => action.mutate('publish')}>
                Publish
              </button>
            ) : (
              <button className="btn-secondary w-full" disabled={action.isPending}
                onClick={() => action.mutate('pause')}>
                Pause
              </button>
            )}
            <button className="btn-danger w-full" disabled={remove.isPending}
              onClick={() => {
                if (confirm('Remove this listing? Buyers will no longer see it.')) remove.mutate()
              }}>
              Remove listing
            </button>
            {actionError && <p role="alert" className="text-sm text-red-700">{actionError.message}</p>}
          </div>
        </div>
      )}
    </div>
  )
}

function PhotoManager({ listing, onChange }: { listing: Listing; onChange: () => void }) {
  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      for (const file of files) {
        const body = new FormData()
        body.append('file', file)
        await api<Photo>(`/api/listings/${listing.id}/photos`, { method: 'POST', body })
      }
    },
    onSettled: onChange,
  })
  const remove = useMutation({
    mutationFn: (photoId: string) =>
      api<void>(`/api/listings/${listing.id}/photos/${photoId}`, { method: 'DELETE' }),
    onSettled: onChange,
  })
  const error = upload.error ?? remove.error

  return (
    <div className="card space-y-3 p-5">
      <h2 className="font-semibold">Photos ({listing.photos.length}/8)</h2>
      <ul className="grid grid-cols-3 gap-2">
        {listing.photos.map((p) => (
          <li key={p.id} className="group relative aspect-square overflow-hidden rounded-lg bg-stone-100">
            <img src={p.thumb_url} alt="" className="h-full w-full object-cover" />
            <button type="button" aria-label="Delete photo"
              className="absolute right-1 top-1 rounded-full bg-white/90 px-2 text-xs font-bold text-red-700 shadow"
              onClick={() => remove.mutate(p.id)}>
              ×
            </button>
          </li>
        ))}
      </ul>
      {listing.photos.length < 8 && (
        <label className="btn-secondary w-full cursor-pointer">
          {upload.isPending ? 'Uploading…' : 'Add photos'}
          <input type="file" accept="image/jpeg,image/png,image/webp" multiple className="sr-only"
            disabled={upload.isPending}
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []).slice(0, 8 - listing.photos.length)
              if (files.length) upload.mutate(files)
              e.target.value = ''
            }} />
        </label>
      )}
      <p className="text-xs text-stone-500">JPEG, PNG or WebP, up to 5 MB each.</p>
      {error && <p role="alert" className="text-sm text-red-700">{error.message}</p>}
    </div>
  )
}
