// Run with: node --test edge/ (Node 24 runs TypeScript directly).
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { escapeHtml, previewTags } from './og.ts'

const listing = {
  id: '3f1c2a52-0000-4000-8000-000000000001',
  title: 'Bicicleta "aro 29" <seminova>',
  description: 'Quadro de alumínio,\n\nfreio a disco & pouco uso.',
  price_cents: 125000,
  currency: 'BRL',
  city: 'Recife',
  state: 'PE',
  photos: [{}],
}

test('escapes everything that could break out of an attribute', () => {
  assert.equal(escapeHtml(`"<a href='x'>&</a>"`), `&quot;&lt;a href='x'&gt;&amp;&lt;/a&gt;&quot;`)
})

test('builds title, description, price and a JPEG image on the site origin', () => {
  const { title, tags } = previewTags(listing, 'https://shop.example/listings/abc?utm=1')
  assert.match(title, /^Bicicleta &quot;aro 29&quot; &lt;seminova&gt; · R\$\s1\.250,00 \| tradepost$/)
  assert.match(tags, /<meta property="og:title" content="Bicicleta &quot;aro 29&quot;/)
  assert.match(tags, /content="Quadro de alumínio, freio a disco &amp; pouco uso\. · Recife - PE"/)
  assert.match(tags, /<meta property="og:image" content="https:\/\/shop\.example\/og\/3f1c2a52-0000-4000-8000-000000000001\.jpg">/)
  assert.match(tags, /<meta property="og:url" content="https:\/\/shop\.example\/listings\/3f1c2a52/)
  assert.match(tags, /<meta property="product:price:amount" content="1250\.00">/)
  assert.doesNotMatch(tags, /<seminova>/)
})

test('a listing without photos gets the small card and no image', () => {
  const { tags } = previewTags({ ...listing, photos: [] }, 'https://shop.example/listings/abc')
  assert.match(tags, /twitter:card" content="summary"/)
  assert.doesNotMatch(tags, /og:image/)
})

test('long descriptions are clipped', () => {
  const { tags } = previewTags({ ...listing, description: 'x'.repeat(500) }, 'https://shop.example/')
  const description = /og:description" content="([^"]*)"/.exec(tags)?.[1] ?? ''
  assert.ok(description.length < 220)
  assert.ok(description.includes('…'))
})
