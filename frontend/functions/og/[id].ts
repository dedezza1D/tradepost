/**
 * Link-preview images (/og/<listing id>.jpg). The API renders the JPEG; the edge keeps it for a
 * day, so each listing's image is rendered about once a day however often the link is shared.
 */
interface Env {
  API_ORIGIN: string
  PROXY_SECRET: string
}

const FILE = /^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jpg$/

export const onRequestGet: PagesFunction<Env> = async ({ params, env, request, waitUntil }) => {
  const match = FILE.exec(String(params.id))
  if (!match) return new Response('Not found', { status: 404 })

  const cache = caches.default
  const cacheKey = new Request(new URL(request.url).origin + `/og/${match[1]}.jpg`)
  const cached = await cache.match(cacheKey)
  if (cached) return cached

  const upstream = await fetch(new URL(`/api/listings/${match[1]}/og.jpg`, env.API_ORIGIN).toString(), {
    headers: { 'X-Proxy-Secret': env.PROXY_SECRET },
  })
  if (!upstream.ok) return new Response('Not found', { status: 404 })
  const response = new Response(upstream.body, {
    headers: {
      'Content-Type': 'image/jpeg',
      'Cache-Control': 'public, max-age=86400',
      'X-Content-Type-Options': 'nosniff',
    },
  })
  waitUntil(cache.put(cacheKey, response.clone()))
  return response
}
