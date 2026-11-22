/**
 * Answers that come from the hosting platform in front of the API instead of the API itself.
 *
 * The API answers every error as JSON with a stable code. While its instance is asleep, being
 * replaced by a deploy or unreachable, the platform answers in its place: a plain-text 429 from
 * the router, a challenge page, an HTML 502/503/504. Passed through unchanged, those reach the
 * app as "too many attempts" or as an error it cannot read. Here they become one JSON shape with
 * its own codes, so the app can wait and try again, and say what is actually happening.
 */
export type UpstreamFailureCode = 'api_starting' | 'api_unavailable'

export interface UpstreamFailure {
  code: UpstreamFailureCode
  /** True when the platform answered before the request reached the API: sending it again
   * cannot run anything twice, whatever the method. */
  replayable: boolean
}

export const RETRY_AFTER_SECONDS = 5

const MESSAGES: Record<UpstreamFailureCode, string> = {
  api_starting: 'The server is starting. Try again in a few seconds.',
  api_unavailable: 'The server is unavailable right now. Try again in a moment.',
}

/** The failure a platform answer stands for, or null when the answer is the API's own. */
export function upstreamFailure(status: number, headers: Headers): UpstreamFailure | null {
  // The router answers for an instance that is asleep or still waking up.
  if ((headers.get('x-render-routing') ?? '').startsWith('hibernate')) {
    return { code: 'api_starting', replayable: true }
  }
  // A challenge page is served in front of the origin; the request stopped there.
  if (headers.has('cf-mitigated')) return { code: 'api_unavailable', replayable: true }
  const fromApi = (headers.get('content-type') ?? '').toLowerCase().includes('application/json')
  if (!fromApi && (status === 429 || status >= 500)) {
    // A gateway error may follow a request the API had already started to handle.
    return { code: 'api_unavailable', replayable: false }
  }
  return null
}

export function failureResponse(failure: UpstreamFailure): Response {
  const body = { error: { code: failure.code, message: MESSAGES[failure.code], replayable: failure.replayable } }
  return new Response(JSON.stringify(body), {
    status: 503,
    headers: {
      'Content-Type': 'application/json',
      'Retry-After': String(RETRY_AFTER_SECONDS),
      'Cache-Control': 'no-store',
    },
  })
}
