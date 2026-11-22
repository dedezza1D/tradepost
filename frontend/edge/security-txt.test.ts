// Run with: node --test edge/ (Node 24 runs TypeScript directly).
// RFC 9116: Contact and Expires are required, Expires is less than a year away when published.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

const fields = new Map<string, string[]>()
for (const line of readFileSync(new URL('../public/.well-known/security.txt', import.meta.url), 'utf8').split(/\r?\n/)) {
  const at = line.indexOf(':')
  if (at <= 0 || line.startsWith('#')) continue
  const name = line.slice(0, at).trim().toLowerCase()
  fields.set(name, [...(fields.get(name) ?? []), line.slice(at + 1).trim()])
}

test('security.txt names a contact and a policy over HTTPS', () => {
  assert.ok((fields.get('contact') ?? []).length > 0)
  for (const url of [...(fields.get('contact') ?? []), ...(fields.get('policy') ?? []), ...(fields.get('canonical') ?? [])]) {
    assert.match(url, /^https:\/\//)
  }
})

test('security.txt has exactly one Expires, in the future and less than a year away', () => {
  const expires = fields.get('expires') ?? []
  assert.equal(expires.length, 1)
  const when = Date.parse(expires[0])
  assert.ok(!Number.isNaN(when), 'Expires is an ISO 8601 date')
  const days = (when - Date.now()) / 86_400_000
  assert.ok(days > 0, 'security.txt has expired')
  assert.ok(days <= 366, 'Expires is more than a year away')
})
