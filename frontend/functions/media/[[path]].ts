/**
 * Serves listing photos and avatars from the R2 bucket bound as MEDIA.
 *
 * Keys are validated so only the processed images the API wrote can be read. Images are
 * immutable (every upload gets a new key), so responses are cached at the edge: repeated views
 * do not reach R2, which keeps read operations far inside the free tier.
 */
interface Env {
  MEDIA: R2Bucket
}

const KEY = /^(listings|avatars)\/[0-9a-f-]{36}\/[0-9a-f-]{36}(_t)?\.webp$/

export const onRequestGet: PagesFunction<Env> = async ({ params, env, request, waitUntil }) => {
  const key = (Array.isArray(params.path) ? params.path : [params.path]).join('/')
  if (!KEY.test(key)) return new Response('Not found', { status: 404 })

  const cache = caches.default
  const cacheKey = new Request(new URL(request.url).toString(), { method: 'GET' })
  const cached = await cache.match(cacheKey)
  if (cached) return cached

  const object = await env.MEDIA.get(key)
  if (object === null) return new Response('Not found', { status: 404 })

  const headers = new Headers()
  object.writeHttpMetadata(headers)
  headers.set('ETag', object.httpEtag)
  headers.set('Cache-Control', 'public, max-age=31536000, immutable')
  headers.set('X-Content-Type-Options', 'nosniff')
  const response = new Response(object.body, { headers })
  waitUntil(cache.put(cacheKey, response.clone()))
  return response
}
