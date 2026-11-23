// Run with: node --test edge/ (Node 24 runs TypeScript directly).
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { classify, HEALTH_PATH, ping } from './keep-warm.ts'

const headers = (init: Record<string, string>) => new Headers(init)
const json = { 'content-type': 'application/json' }

test('a healthy answer from the API is up', () => {
  assert.equal(classify(200, headers(json), '{"status":"ok"}'), 'up')
})

test('the router of a sleeping or waking instance means asleep', () => {
  for (const routing of ['hibernate-wake', 'hibernate-rate-limited']) {
    assert.equal(classify(429, headers({ 'x-render-routing': routing }), 'Too Many Requests'), 'asleep')
  }
})

test('a challenge from the edge is told apart from a sleeping instance', () => {
  const page = '<!DOCTYPE html><title>Just a moment...</title>'
  assert.equal(classify(429, headers({ 'cf-mitigated': 'challenge', 'content-type': 'text/html' }), page), 'challenged')
})

test('anything else is down, a 200 that is not the health check included', () => {
  assert.equal(classify(503, headers({ 'content-type': 'text/html' }), '<html>'), 'down')
  assert.equal(classify(200, headers({ 'content-type': 'text/html' }), '<div id="root">'), 'down')
  assert.equal(classify(200, headers(json), '{"status":"degraded"}'), 'down')
})

test('the ping asks the origin health check and reports what answered', async () => {
  const asked: string[] = []
  const fetcher = (async (input: RequestInfo | URL) => {
    asked.push(String(input))
    return new Response('{"status":"ok"}', { status: 200, headers: json })
  }) as typeof fetch
  const result = await ping('https://api.example.test', fetcher)
  assert.deepEqual(asked, [`https://api.example.test${HEALTH_PATH}`])
  assert.equal(result.outcome, 'up')
  assert.equal(result.status, 200)
})

test('the ping names the platform header when the instance is asleep', async () => {
  const fetcher = (async () =>
    new Response('Too Many Requests', { status: 429, headers: { 'x-render-routing': 'hibernate-rate-limited' } })) as typeof fetch
  const result = await ping('https://api.example.test', fetcher)
  assert.equal(result.outcome, 'asleep')
  assert.equal(result.detail, 'hibernate-rate-limited')
})

test('a network failure is unreachable, not an exception', async () => {
  const fetcher = (async () => {
    throw new TypeError('network connection lost')
  }) as typeof fetch
  const result = await ping('https://api.example.test', fetcher)
  assert.equal(result.outcome, 'unreachable')
  assert.equal(result.status, null)
})
