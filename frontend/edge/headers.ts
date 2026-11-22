/**
 * Security headers for answers built by Functions.
 *
 * `public/_headers` covers static files, including the page `/listings/:id` takes from `next()`.
 * Answers a Function builds itself (the API proxy, photos, preview images) do not get those
 * headers, so the middleware of each of those route families adds them here. Headers already
 * set by the upstream answer win.
 */

// Same value as in public/_headers (a test keeps them equal).
export const HSTS = 'max-age=31536000; includeSubDomains'

// JSON, images and plain text never need to load anything or be framed.
export const NON_HTML_CSP = "default-src 'none'; frame-ancestors 'none'"

const COMMON: ReadonlyArray<[string, string]> = [
  ['Strict-Transport-Security', HSTS],
  ['X-Content-Type-Options', 'nosniff'],
  ['X-Frame-Options', 'DENY'],
  ['Referrer-Policy', 'strict-origin-when-cross-origin'],
]

export function withSecurityHeaders(response: Response): Response {
  // A copy: answers from fetch() and the edge cache have immutable headers. The body is passed
  // along as a stream, so event streams keep flowing.
  const out = new Response(response.body, response)
  for (const [name, value] of COMMON) {
    if (!out.headers.has(name)) out.headers.set(name, value)
  }
  const type = out.headers.get('Content-Type') ?? ''
  if (!type.includes('text/html') && !out.headers.has('Content-Security-Policy')) {
    out.headers.set('Content-Security-Policy', NON_HTML_CSP)
  }
  return out
}
