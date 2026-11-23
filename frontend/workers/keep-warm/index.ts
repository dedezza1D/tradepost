/**
 * Scheduled Worker that keeps the API's free instance awake (see edge/keep-warm.ts).
 *
 * It has no HTTP handler and no public URL: it only runs on its cron trigger and writes one log
 * line per run, so the Worker's logs show when the instance was found asleep or challenged.
 */
import { ping } from '../../edge/keep-warm'

interface Env {
  /** The API origin, the same value as the Pages proxy's API_ORIGIN. Set as a secret. */
  API_ORIGIN: string
}

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(run(env))
  },
} satisfies ExportedHandler<Env>

async function run(env: Env): Promise<void> {
  if (!env.API_ORIGIN) {
    console.error('keep-warm: API_ORIGIN is not set')
    return
  }
  const result = await ping(env.API_ORIGIN)
  const line = ['keep-warm:', result.outcome, result.status ?? '-', result.detail, `${result.elapsedMs}ms`]
  if (result.outcome === 'up') console.log(...line)
  else console.warn(...line)
}
