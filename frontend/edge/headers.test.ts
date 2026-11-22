// Run with: node --test edge/ (Node 24 runs TypeScript directly).
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { HSTS, NON_HTML_CSP, withSecurityHeaders } from './headers.ts'

const middlewareDirs = ['api', 'media', 'og']

function staticHeaders(): Map<string, string> {
  // The "/*" block of public/_headers: what static files, /listings/:id included, receive.
  const text = readFileSync(new URL('../public/_headers', import.meta.url), 'utf8')
  const block = text.split(/\n(?=\S)/).find((part) => part.startsWith('/*'))
  assert.ok(block, 'public/_headers has a /* block')
  const headers = new Map<string, string>()
  for (const line of block.split('\n').slice(1)) {
    const at = line.indexOf(':')
    if (at > 0) headers.set(line.slice(0, at).trim().toLowerCase(), line.slice(at + 1).trim())
  }
  return headers
}

test('API answers get the security headers and a deny-all CSP', async () => {
  const api = new Response('{"ok":true}', { status: 201, headers: { 'Content-Type': 'application/json' } })
  const out = withSecurityHeaders(api)
  assert.equal(out.status, 201)
  assert.equal(out.headers.get('Strict-Transport-Security'), HSTS)
  assert.equal(out.headers.get('X-Content-Type-Options'), 'nosniff')
  assert.equal(out.headers.get('X-Frame-Options'), 'DENY')
  assert.equal(out.headers.get('Referrer-Policy'), 'strict-origin-when-cross-origin')
  assert.equal(out.headers.get('Content-Security-Policy'), NON_HTML_CSP)
  assert.equal(await out.text(), '{"ok":true}')
})

test('photos keep their own headers and gain the missing ones', () => {
  const photo = new Response('webp', {
    headers: {
      'Content-Type': 'image/webp',
      'Cache-Control': 'public, max-age=31536000, immutable',
      'X-Content-Type-Options': 'nosniff',
    },
  })
  const out = withSecurityHeaders(photo)
  assert.equal(out.headers.get('Cache-Control'), 'public, max-age=31536000, immutable')
  assert.equal(out.headers.get('Strict-Transport-Security'), HSTS)
  assert.equal(out.headers.get('Content-Security-Policy'), NON_HTML_CSP)
})

test('headers set upstream win', () => {
  const upstream = new Response('{}', {
    headers: { 'Content-Type': 'application/json', 'Referrer-Policy': 'no-referrer' },
  })
  assert.equal(withSecurityHeaders(upstream).headers.get('Referrer-Policy'), 'no-referrer')
})

test('HTML is not given the deny-all CSP', () => {
  const page = new Response('<!doctype html>', { headers: { 'Content-Type': 'text/html; charset=utf-8' } })
  const out = withSecurityHeaders(page)
  assert.equal(out.headers.get('Content-Security-Policy'), null)
  assert.equal(out.headers.get('X-Frame-Options'), 'DENY')
})

test('answers with immutable headers and streamed bodies are copied, not broken', async () => {
  const cached = Response.redirect('https://example.com/', 302) // immutable headers
  assert.equal(withSecurityHeaders(cached).headers.get('Location'), 'https://example.com/')

  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('data: hello\n\n'))
      controller.close()
    },
  })
  const events = withSecurityHeaders(new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } }))
  assert.equal(await events.text(), 'data: hello\n\n')
})

test('static files get the same HSTS and the core headers from public/_headers', () => {
  const headers = staticHeaders()
  assert.equal(headers.get('strict-transport-security'), HSTS)
  assert.equal(headers.get('x-content-type-options'), 'nosniff')
  assert.equal(headers.get('x-frame-options'), 'DENY')
  assert.match(headers.get('content-security-policy') ?? '', /frame-ancestors 'none'/)
})

test('every route family a Function builds answers for has the middleware', () => {
  for (const dir of middlewareDirs) {
    const source = readFileSync(new URL(`../functions/${dir}/_middleware.ts`, import.meta.url), 'utf8')
    assert.match(source, /withSecurityHeaders\(await next\(\)\)/, `functions/${dir}/_middleware.ts`)
  }
})
