// Run with: node --test edge/ (Node 24 runs TypeScript directly).
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { failureResponse, upstreamFailure } from './upstream.ts'

const headers = (init: Record<string, string>) => new Headers(init)

test('the API’s own answers pass through, errors included', () => {
  assert.equal(upstreamFailure(200, headers({ 'content-type': 'application/json' })), null)
  assert.equal(upstreamFailure(429, headers({ 'content-type': 'application/json', 'retry-after': '60' })), null)
  assert.equal(upstreamFailure(500, headers({ 'content-type': 'application/json' })), null)
  assert.equal(upstreamFailure(404, headers({ 'content-type': 'application/json' })), null)
})

test('answers without a JSON body pass through when they are not failures', () => {
  assert.equal(upstreamFailure(204, headers({})), null)
  assert.equal(upstreamFailure(200, headers({ 'content-type': 'text/event-stream' })), null)
  assert.equal(upstreamFailure(200, headers({ 'content-type': 'image/jpeg' })), null)
})

test('a 429 from the router of a sleeping instance means the API is starting', () => {
  const failure = upstreamFailure(
    429,
    headers({ 'content-type': 'text/plain; charset=utf-8', 'x-render-routing': 'hibernate-rate-limited' }),
  )
  assert.deepEqual(failure, { code: 'api_starting', replayable: true })
})

test('a challenge page stopped the request before the API', () => {
  const failure = upstreamFailure(429, headers({ 'content-type': 'text/html; charset=UTF-8', 'cf-mitigated': 'challenge' }))
  assert.deepEqual(failure, { code: 'api_unavailable', replayable: true })
})

test('gateway errors are unavailable, and not safe to send again blindly', () => {
  for (const status of [429, 500, 502, 503, 504]) {
    assert.deepEqual(upstreamFailure(status, headers({ 'content-type': 'text/html' })), {
      code: 'api_unavailable',
      replayable: false,
    })
  }
  assert.deepEqual(upstreamFailure(502, headers({})), { code: 'api_unavailable', replayable: false })
})

test('the failure is answered in the API’s error shape, with a retry hint and no caching', async () => {
  const response = failureResponse({ code: 'api_starting', replayable: true })
  assert.equal(response.status, 503)
  assert.equal(response.headers.get('content-type'), 'application/json')
  assert.equal(response.headers.get('retry-after'), '5')
  assert.equal(response.headers.get('cache-control'), 'no-store')
  assert.deepEqual(await response.json(), {
    error: { code: 'api_starting', message: 'The server is starting. Try again in a few seconds.', replayable: true },
  })
})
