import { beginWaiting } from './status'

export interface FieldError {
  loc: (string | number)[]
  message: string
  type: string
}

interface ApiErrorDetails {
  fields?: FieldError[]
  /** The request never reached the API, so sending it again cannot run anything twice. */
  replayable?: boolean
  retryAfterSeconds?: number
  /** Id of the request in the API's logs. */
  requestId?: string
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly fields: FieldError[]
  readonly replayable: boolean
  readonly retryAfterSeconds?: number
  readonly requestId?: string

  constructor(status: number, code: string, message: string, details: ApiErrorDetails = {}) {
    super(message)
    this.status = status
    this.code = code
    this.fields = details.fields ?? []
    this.replayable = details.replayable ?? false
    this.retryAfterSeconds = details.retryAfterSeconds
    this.requestId = details.requestId
  }

  /** The API did not answer: it is starting, being replaced or unreachable. */
  get unavailable(): boolean {
    return this.code === 'api_starting' || this.code === 'api_unavailable'
  }

  /** Message for one form field, from a 422 validation error. */
  fieldMessage(field: string): string | undefined {
    return this.fields.find((f) => f.loc[f.loc.length - 1] === field)?.message
  }
}

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

// An instance that was asleep takes up to about a minute to answer again.
const STARTING_WAIT_MS = 45_000
const DEFAULT_RETRY_MS = 5_000
const MAX_RETRY_MS = 10_000
// When it is unknown whether the API saw the request, only reads are repeated, and briefly.
const UNKNOWN_RETRY_MS = [2_000, 4_000]

function readCookie(name: string): string | undefined {
  return document.cookie
    .split('; ')
    .find((row) => row.startsWith(`${name}=`))
    ?.split('=')[1]
}

/** How long to wait before sending the request again, or null to give up. */
export function retryDelayMs(error: unknown, method: string, attempt: number, elapsedMs: number): number | null {
  if (!(error instanceof ApiError) || !error.unavailable) return null
  if (error.replayable) {
    const hinted = error.retryAfterSeconds ? error.retryAfterSeconds * 1000 : DEFAULT_RETRY_MS
    const delay = Math.min(Math.max(hinted, 1_000), MAX_RETRY_MS)
    return elapsedMs + delay <= STARTING_WAIT_MS ? delay : null
  }
  if (!SAFE_METHODS.has(method)) return null
  return UNKNOWN_RETRY_MS[attempt] ?? null
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

async function send<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (response.status === 204) return undefined as T
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const error = body?.error
    // The API always answers errors as JSON; a failure without one came from the
    // infrastructure in front of it.
    const fallback = response.status === 429 || response.status >= 500 ? 'api_unavailable' : 'http_error'
    const retryAfter = Number(response.headers.get('Retry-After'))
    throw new ApiError(response.status, error?.code ?? fallback, error?.message ?? `Request failed (${response.status})`, {
      fields: error?.fields,
      replayable: error?.replayable === true,
      retryAfterSeconds: Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : undefined,
      requestId: error?.request_id ?? response.headers.get('X-Request-ID') ?? undefined,
    })
  }
  return body as T
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }
  if (!SAFE_METHODS.has(method)) {
    const csrf = readCookie('tp_csrf')
    if (csrf) headers.set('X-CSRF-Token', csrf)
  }
  const request: RequestInit = { ...init, method, headers, credentials: 'same-origin' }

  const startedAt = Date.now()
  let endWaiting: (() => void) | undefined
  try {
    for (let attempt = 0; ; attempt += 1) {
      try {
        return await send<T>(path, request)
      } catch (error) {
        const delay = retryDelayMs(error, method, attempt, Date.now() - startedAt)
        if (delay === null) throw error
        endWaiting ??= beginWaiting()
        await sleep(delay)
      }
    }
  } finally {
    endWaiting?.()
  }
}

export const json = (data: unknown): string => JSON.stringify(data)
