// Captures the main screens (phone and desktop, light and dark) for visual review.
//   node scripts/screenshots.mjs <out-dir> [base-url]
// Signs in with TP_SHOT_EMAIL / TP_SHOT_PASSWORD when set (a local test account).
import { chromium, devices } from '@playwright/test'
import { mkdirSync } from 'node:fs'

const out = process.argv[2] ?? 'screenshots'
const base = process.argv[3] ?? 'http://localhost:5180'
mkdirSync(out, { recursive: true })

const pages = [
  ['home', '/'],
  ['search', '/search?q=camera'],
  ['listing', null],
  ['sell', '/sell'],
  ['profile', '/me'],
  ['seller', '/seller'],
]

const browser = await chromium.launch()
for (const [device, viewport] of [
  ['phone', devices['Pixel 7']],
  ['desktop', { viewport: { width: 1366, height: 860 } }],
]) {
  for (const scheme of ['light', 'dark']) {
    const context = await browser.newContext({ ...viewport, colorScheme: scheme, locale: 'pt-BR' })
    const page = await context.newPage()
    if (process.env.TP_SHOT_EMAIL) {
      const r = await page.request.post(`${base}/api/auth/login`, {
        data: { email: process.env.TP_SHOT_EMAIL, password: process.env.TP_SHOT_PASSWORD },
        headers: { Origin: base },
      })
      if (!r.ok()) throw new Error(`login failed: ${r.status()}`)
    }
    const listings = await (await page.request.get(`${base}/api/listings?limit=1`)).json()
    for (const [name, path] of pages) {
      const target = path ?? `/listings/${listings.items[0]?.id}`
      await page.goto(base + target, { waitUntil: 'networkidle' })
      await page.waitForTimeout(400)
      await page.screenshot({ path: `${out}/${device}-${scheme}-${name}.png`, fullPage: device === 'desktop' })
    }
    await context.close()
  }
}
await browser.close()
console.log('screenshots in', out)
