/**
 * Listing pages with link-preview tags.
 *
 * The app is a single-page app, so crawlers of messaging apps and social networks would only
 * see the empty shell. This adds the listing's title, description and image to the HTML before
 * it leaves the edge. Browsers get the same page and boot the app as usual. If the API is slow
 * (asleep on the free tier) or the listing is not public, the plain page is served unchanged.
 */
import { previewTags, type ListingForPreview } from '../../edge/og'

interface Env {
  API_ORIGIN: string
  PROXY_SECRET: string
}

const ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const API_TIMEOUT_MS = 2500
const CACHE_SECONDS = 300

async function loadListing(id: string, env: Env, waitUntil: (p: Promise<unknown>) => void): Promise<ListingForPreview | null> {
  // Cached per listing for a few minutes, so a link shared in a group does not hit the API
  // once per member.
  const cacheKey = new Request(`https://preview.cache/listings/${id}`)
  const cache = caches.default
  const cached = await cache.match(cacheKey)
  if (cached) return (await cached.json()) as ListingForPreview

  try {
    const response = await fetch(new URL(`/api/listings/${id}`, env.API_ORIGIN).toString(), {
      headers: { 'X-Proxy-Secret': env.PROXY_SECRET, Accept: 'application/json' },
      signal: AbortSignal.timeout(API_TIMEOUT_MS),
    })
    if (!response.ok) return null
    const listing = (await response.json()) as ListingForPreview
    waitUntil(
      cache.put(
        cacheKey,
        new Response(JSON.stringify(listing), {
          headers: { 'Content-Type': 'application/json', 'Cache-Control': `public, max-age=${CACHE_SECONDS}` },
        }),
      ),
    )
    return listing
  } catch {
    return null
  }
}

export const onRequestGet: PagesFunction<Env> = async ({ params, env, request, next, waitUntil }) => {
  const page = await next()
  const id = String(params.id)
  if (!ID.test(id) || !(page.headers.get('Content-Type') ?? '').includes('text/html')) return page

  const listing = await loadListing(id, env, waitUntil)
  if (!listing) return page

  const { title, tags } = previewTags(listing, request.url)
  return new HTMLRewriter()
    .on('title', { element: (el) => void el.setInnerContent(title, { html: true }) })
    .on('head', { element: (el) => void el.append(tags, { html: true }) })
    .transform(page)
}
