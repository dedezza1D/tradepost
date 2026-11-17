/**
 * Same-origin proxy to the API.
 *
 * The browser only ever talks to the Pages domain, so session cookies are first-party and CORS
 * stays closed. The API accepts requests only with the shared secret added here, which also
 * makes the client IP below trustworthy (nobody can reach the API around this proxy).
 */
interface Env {
  API_ORIGIN: string
  PROXY_SECRET: string
}

const HOP_BY_HOP = ['connection', 'keep-alive', 'proxy-connection', 'transfer-encoding', 'upgrade']

export const onRequest: PagesFunction<Env> = async ({ request, env }) => {
  const url = new URL(request.url)
  const target = new URL(url.pathname + url.search, env.API_ORIGIN)

  const headers = new Headers(request.headers)
  for (const name of HOP_BY_HOP) headers.delete(name)
  headers.set('X-Proxy-Secret', env.PROXY_SECRET)
  headers.set('X-Client-IP', request.headers.get('CF-Connecting-IP') ?? '')
  headers.set('X-Forwarded-Proto', 'https')

  const hasBody = !['GET', 'HEAD'].includes(request.method)
  const response = await fetch(target.toString(), {
    method: request.method,
    headers,
    // Streamed unchanged: webhook signatures are computed over the exact bytes.
    body: hasBody ? request.body : undefined,
    redirect: 'manual',
  })
  return new Response(response.body, response)
}
