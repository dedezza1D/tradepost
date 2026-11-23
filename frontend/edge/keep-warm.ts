/**
 * Keeps the API's free instance awake.
 *
 * The free instance sleeps after 15 minutes without inbound traffic. Once it is asleep, the
 * platform's edge challenges most requests sent by the web app's proxy before they reach the
 * router, so a visitor's request seldom wakes it and the app reports the server as unavailable
 * for minutes. A scheduled request to the health check every few minutes keeps the instance from
 * going to sleep. The health check is the one route the API serves without the proxy secret.
 */
export type PingOutcome =
  /** The API answered its health check. */
  | 'up'
  /** The router answered for a sleeping or waking instance. */
  | 'asleep'
  /** The platform's edge answered with a challenge; the request never reached the router. */
  | 'challenged'
  /** Something answered, but not a healthy API. */
  | 'down'
  /** No answer at all (network error or timeout). */
  | 'unreachable'

export interface PingResult {
  outcome: PingOutcome
  status: number | null
  /** The platform header that explains a failure, when there is one. */
  detail: string
  elapsedMs: number
}

export const HEALTH_PATH = '/healthz'
/** A waking instance holds the request for about a minute before it answers. */
export const PING_TIMEOUT_MS = 90_000

export function classify(status: number, headers: Headers, body: string): PingOutcome {
  if ((headers.get('x-render-routing') ?? '').startsWith('hibernate')) return 'asleep'
  if (headers.has('cf-mitigated')) return 'challenged'
  if (status === 200 && isHealthy(body)) return 'up'
  return 'down'
}

function isHealthy(body: string): boolean {
  try {
    return (JSON.parse(body) as { status?: unknown }).status === 'ok'
  } catch {
    return false
  }
}

export async function ping(origin: string, fetcher: typeof fetch = fetch): Promise<PingResult> {
  const started = Date.now()
  try {
    const response = await fetcher(new URL(HEALTH_PATH, origin).toString(), {
      redirect: 'manual',
      signal: AbortSignal.timeout(PING_TIMEOUT_MS),
    })
    const body = await response.text()
    return {
      outcome: classify(response.status, response.headers, body),
      status: response.status,
      detail: response.headers.get('x-render-routing') ?? response.headers.get('cf-mitigated') ?? '',
      elapsedMs: Date.now() - started,
    }
  } catch {
    return { outcome: 'unreachable', status: null, detail: '', elapsedMs: Date.now() - started }
  }
}
