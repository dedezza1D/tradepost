/**
 * Link-preview (Open Graph) tags for a listing page. Kept free of Workers APIs so it can be
 * unit-tested with plain Node.
 */

export interface ListingForPreview {
  id: string
  title: string
  description: string
  price_cents: number
  currency: string
  city: string
  state: string
  photos: unknown[]
}

export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

export function formatPrice(cents: number, currency: string): string {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency }).format(cents / 100)
}

function clip(text: string, max: number): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length <= max ? flat : `${flat.slice(0, max - 1).trimEnd()}…`
}

/** The page title and the <meta> tags to add to <head>, already escaped. */
export function previewTags(listing: ListingForPreview, pageUrl: string): { title: string; tags: string } {
  const origin = new URL(pageUrl).origin
  const price = formatPrice(listing.price_cents, listing.currency)
  const title = `${clip(listing.title, 90)} · ${price}`
  const description = `${clip(listing.description, 180)} · ${listing.city} - ${listing.state}`
  const meta: [string, string, string][] = [
    ['name', 'description', description],
    ['property', 'og:type', 'product'],
    ['property', 'og:site_name', 'tradepost'],
    ['property', 'og:title', title],
    ['property', 'og:description', description],
    ['property', 'og:url', `${origin}/listings/${listing.id}`],
    ['property', 'product:price:amount', (listing.price_cents / 100).toFixed(2)],
    ['property', 'product:price:currency', listing.currency],
    ['name', 'twitter:card', listing.photos.length ? 'summary_large_image' : 'summary'],
    ['name', 'twitter:title', title],
    ['name', 'twitter:description', description],
  ]
  if (listing.photos.length) {
    const image = `${origin}/og/${listing.id}.jpg`
    meta.push(
      ['property', 'og:image', image],
      ['property', 'og:image:type', 'image/jpeg'],
      ['property', 'og:image:width', '1200'],
      ['property', 'og:image:height', '630'],
      ['property', 'og:image:alt', clip(listing.title, 120)],
      ['name', 'twitter:image', image],
    )
  }
  const tags = meta.map(([attr, key, value]) => `<meta ${attr}="${key}" content="${escapeHtml(value)}">`).join('')
  return { title: escapeHtml(`${title} | tradepost`), tags }
}
