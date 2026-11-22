// Run with: node --test edge/ (Node 24 runs TypeScript directly).
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { MAX_BODY_BYTES, bodyTooLarge, tooLargeResponse } from './limits.ts'

const withLength = (value: string) => new Headers({ 'content-length': value })

test('bodies within the ceiling, or without a declared length, go through', () => {
  assert.equal(bodyTooLarge(new Headers()), false)
  assert.equal(bodyTooLarge(withLength('0')), false)
  assert.equal(bodyTooLarge(withLength(String(MAX_BODY_BYTES))), false)
})

test('bodies over the ceiling or with an invalid length are refused', () => {
  assert.equal(bodyTooLarge(withLength(String(MAX_BODY_BYTES + 1))), true)
  assert.equal(bodyTooLarge(withLength('abc')), true)
  assert.equal(bodyTooLarge(withLength('-1')), true)
  assert.equal(bodyTooLarge(withLength('1.5')), true)
})

test('the refusal is a 413 in the API error shape, not cached', async () => {
  const response = tooLargeResponse()
  assert.equal(response.status, 413)
  assert.equal(response.headers.get('cache-control'), 'no-store')
  assert.deepEqual(await response.json(), {
    error: { code: 'payload_too_large', message: 'Request body too large.' },
  })
})
