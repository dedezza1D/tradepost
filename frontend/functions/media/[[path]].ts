/** Serves listing photos from the R2 bucket bound as MEDIA. Keys are validated so only the
 * processed photos the API wrote can be read. */
interface Env {
  MEDIA: R2Bucket
}

const KEY = /^listings\/[0-9a-f-]{36}\/[0-9a-f-]{36}(_t)?\.webp$/

export const onRequestGet: PagesFunction<Env> = async ({ params, env, request }) => {
  const key = (Array.isArray(params.path) ? params.path : [params.path]).join('/')
  if (!KEY.test(key)) return new Response('Not found', { status: 404 })

  const object = await env.MEDIA.get(key, { onlyIf: request.headers })
  if (object === null) return new Response('Not found', { status: 404 })

  const headers = new Headers()
  object.writeHttpMetadata(headers)
  headers.set('ETag', object.httpEtag)
  headers.set('Cache-Control', 'public, max-age=31536000, immutable')
  headers.set('X-Content-Type-Options', 'nosniff')
  if (!('body' in object)) return new Response(null, { status: 304, headers })
  return new Response(object.body, { headers })
}
