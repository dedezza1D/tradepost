/**
 * Ceiling on request bodies forwarded by the API proxy.
 *
 * The API enforces a ceiling per route (JSON, one photo, three photos for a listing draft,
 * webhooks). This one is a coarse first line: a body declared larger than the API's biggest
 * ceiling is refused here, without being sent to the origin.
 */
export const MAX_BODY_BYTES = 16 * 1024 * 1024

/** True when the declared Content-Length is invalid or over the ceiling. */
export function bodyTooLarge(headers: Headers): boolean {
  const declared = headers.get('content-length')
  if (declared === null) return false
  const size = Number(declared)
  return !Number.isInteger(size) || size < 0 || size > MAX_BODY_BYTES
}

export function tooLargeResponse(): Response {
  const body = { error: { code: 'payload_too_large', message: 'Request body too large.' } }
  return new Response(JSON.stringify(body), {
    status: 413,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
  })
}
