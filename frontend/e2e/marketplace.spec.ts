import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Browser, type Page } from '@playwright/test'
import { deflateSync } from 'node:zlib'

/** A small solid-color PNG, built in memory (no fixture files). */
function png(width = 320, height = 240): Buffer {
  const crcTable = Array.from({ length: 256 }, (_, n) => {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    return c >>> 0
  })
  const crc = (buf: Buffer) => {
    let c = 0xffffffff
    for (const b of buf) c = crcTable[(c ^ b) & 0xff] ^ (c >>> 8)
    return (c ^ 0xffffffff) >>> 0
  }
  const chunk = (type: string, data: Buffer) => {
    const len = Buffer.alloc(4)
    len.writeUInt32BE(data.length)
    const body = Buffer.concat([Buffer.from(type), data])
    const sum = Buffer.alloc(4)
    sum.writeUInt32BE(crc(body))
    return Buffer.concat([len, body, sum])
  }
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(width, 0)
  ihdr.writeUInt32BE(height, 4)
  ihdr[8] = 8
  ihdr[9] = 2
  const row = Buffer.concat([Buffer.from([0]), Buffer.alloc(width * 3, 0x3c)])
  const raw = Buffer.concat(Array.from({ length: height }, () => row))
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

const unique = () => Math.random().toString(36).slice(2, 8)

async function newUser(browser: Browser, locale: 'pt-BR' | 'en', project: string) {
  const context = await browser.newContext({ ...test.info().project.use, locale })
  const page = await context.newPage()
  const email = `e2e-${project}-${unique()}@example.com`
  return { page, email }
}

async function register(page: Page, name: string, email: string, city: string, title: RegExp) {
  await page.goto('/register')
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  await page.locator('#display_name').fill(name)
  await page.locator('#email').fill(email)
  await page.locator('#password').fill('e2e password that is long')
  await page.getByRole('combobox').first().fill(city)
  await page.getByRole('option', { name: new RegExp(`^${city}`) }).first().click()
  await page.locator('form button[class*="btn-primary"]').click()
  await expect(page).toHaveURL('/')
}

test('a seller lists an item and a buyer buys, receives and reviews it', async ({ browser }, info) => {
  const title = `Bicicleta e2e ${unique()}`

  // Seller, Portuguese interface.
  const seller = await newUser(browser, 'pt-BR', info.project.name)
  await register(seller.page, 'Vendedora E2E', seller.email, 'Recife', /Crie sua conta/)
  await seller.page.goto('/sell')
  await expect(seller.page.getByRole('heading', { name: 'Comece pelas fotos' })).toBeVisible()
  await seller.page.locator('input[type=file]').setInputFiles({ name: 'bike.png', mimeType: 'image/png', buffer: png() })
  await expect(seller.page.getByAltText('Foto 1')).toBeVisible()
  await seller.page.getByRole('button', { name: 'Continuar' }).click()
  await seller.page.locator('#title').fill(title)
  await seller.page.locator('#description').fill('Quadro de alumínio, pouco uso, revisada.')
  await seller.page.locator('#category').selectOption('bikes')
  await seller.page.getByRole('button', { name: 'Continuar' }).click()
  await seller.page.locator('#price').fill('1.250,00')
  await seller.page.locator('#shipping').fill('30')
  await seller.page.getByRole('button', { name: 'Continuar' }).click()
  await expect(seller.page.getByText('R$ 1.250,00')).toBeVisible()
  await seller.page.getByRole('button', { name: 'Publicar' }).click()
  await expect(seller.page.getByRole('heading', { name: title })).toBeVisible()

  // Buyer, English interface.
  const buyer = await newUser(browser, 'en', info.project.name)
  await register(buyer.page, 'Buyer E2E', buyer.email, 'Olinda', /Create your account/)
  await buyer.page.goto(`/search?q=${encodeURIComponent(title)}`)
  await buyer.page.getByRole('link', { name: new RegExp(title) }).first().click()
  await expect(buyer.page.getByRole('heading', { name: title })).toBeVisible()
  await buyer.page.locator('button:visible', { hasText: /Buy (now|for)/ }).first().click()
  await expect(buyer.page.getByRole('heading', { name: 'Checkout' })).toBeVisible()
  await buyer.page.getByRole('button', { name: /Continue to payment/ }).click()
  await buyer.page.getByRole('button', { name: 'Pay with a test card' }).click()
  await expect(buyer.page.getByRole('heading', { name: 'Payment confirmed' })).toBeVisible()

  // The seller is told about the sale and ships it.
  await seller.page.goto('/notifications')
  await expect(seller.page.getByText(/Você vendeu/)).toBeVisible()
  await seller.page.goto('/seller')
  await seller.page.getByPlaceholder('Código de rastreio').fill('BR123456789BR')
  await seller.page.getByRole('button', { name: 'Marcar enviado' }).click()
  await expect(seller.page.getByText('BR123456789BR')).toBeVisible()

  // The buyer confirms delivery and reviews.
  await buyer.page.goto('/orders')
  await buyer.page.getByRole('button', { name: 'I received it' }).click()
  await buyer.page.getByRole('button', { name: '4 out of 5 stars' }).click()
  await buyer.page.getByPlaceholder('How did it go? (optional)').fill('Great bike, as described.')
  await buyer.page.getByRole('button', { name: 'Send review' }).click()
  await expect(buyer.page.getByText('Thanks for your review.')).toBeVisible()

  await seller.page.goto('/seller')
  await expect(seller.page.getByText('Entregue', { exact: true })).toBeVisible()
})

test('main pages have no serious accessibility violations', async ({ page }) => {
  for (const path of ['/', '/search', '/login', '/register']) {
    await page.goto(path)
    await page.waitForLoadState('networkidle')
    const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
    const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
    expect(serious.map((v) => `${path}: ${v.id} (${v.nodes.length})`)).toEqual([])
  }
})
