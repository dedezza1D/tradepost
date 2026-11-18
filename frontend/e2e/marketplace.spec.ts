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

/** Lists an item through the wizard (Portuguese interface) and lands on its page. */
async function listItem(page: Page, title: string) {
  await page.goto('/sell')
  await expect(page.getByRole('heading', { name: 'Comece pelas fotos' })).toBeVisible()
  await page.locator('input[type=file]').setInputFiles({ name: 'bike.png', mimeType: 'image/png', buffer: png() })
  await expect(page.getByAltText('Foto 1')).toBeVisible()
  await page.getByRole('button', { name: 'Continuar' }).click()
  await page.locator('#title').fill(title)
  await page.locator('#description').fill('Quadro de alumínio, pouco uso, revisada.')
  await page.locator('#category').selectOption('bikes')
  await page.getByRole('button', { name: 'Continuar' }).click()
  await page.locator('#price').fill('1.250,00')
  await page.locator('#shipping').fill('30')
  await page.getByRole('button', { name: 'Continuar' }).click()
  await expect(page.getByText('R$ 1.250,00')).toBeVisible()
  await page.getByRole('button', { name: 'Publicar' }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
}

test('a seller lists an item and a buyer buys, receives and reviews it', async ({ browser }, info) => {
  const title = `Bicicleta e2e ${unique()}`

  // Seller, Portuguese interface.
  const seller = await newUser(browser, 'pt-BR', info.project.name)
  await register(seller.page, 'Vendedora E2E', seller.email, 'Recife', /Crie sua conta/)
  await listItem(seller.page, title)

  // Buyer, English interface.
  const buyer = await newUser(browser, 'en', info.project.name)
  await register(buyer.page, 'Buyer E2E', buyer.email, 'Olinda', /Create your account/)
  await buyer.page.goto(`/search?q=${encodeURIComponent(title)}`)
  await buyer.page.getByRole('link', { name: new RegExp(title) }).first().click()
  await expect(buyer.page.getByRole('heading', { name: title })).toBeVisible()

  // Social: save the listing, follow the seller and find the listing in the Following tab.
  const listingUrl = buyer.page.url()
  await buyer.page.getByRole('button', { name: `Save ${title}` }).click()
  await expect(buyer.page.getByRole('button', { name: `Remove ${title} from saved` })).toHaveAttribute('aria-pressed', 'true')
  await buyer.page.getByRole('link', { name: /Vendedora E2E/ }).first().click()
  await expect(buyer.page.getByRole('heading', { name: 'Vendedora E2E' })).toBeVisible()
  await buyer.page.getByRole('button', { name: 'Follow', exact: true }).click()
  await expect(buyer.page.getByRole('button', { name: 'Following', exact: true })).toBeVisible()
  await buyer.page.goto('/?tab=following')
  await expect(buyer.page.getByRole('link', { name: new RegExp(title) })).toBeVisible()
  await buyer.page.goto('/me/saved')
  await expect(buyer.page.getByRole('link', { name: new RegExp(title) })).toBeVisible()
  await buyer.page.goto(listingUrl)

  await buyer.page.locator('button:visible', { hasText: /Buy (now|for)/ }).first().click()
  await expect(buyer.page.getByRole('heading', { name: 'Checkout' })).toBeVisible()
  await buyer.page.getByRole('button', { name: /Continue to payment/ }).click()
  await buyer.page.getByRole('button', { name: 'Pay with a test card' }).click()
  await expect(buyer.page.getByRole('heading', { name: 'Payment confirmed' })).toBeVisible()

  // The seller is told about the sale and ships it.
  await seller.page.goto('/notifications')
  await expect(seller.page.getByText(/Você vendeu/)).toBeVisible()
  await expect(seller.page.getByText(/começou a seguir você/)).toBeVisible()
  await expect(seller.page.getByText(/salvou/)).toBeVisible()
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

test('buyer and seller chat in real time and settle on an offer', async ({ browser }, info) => {
  const title = `Bicicleta chat ${unique()}`
  const seller = await newUser(browser, 'pt-BR', info.project.name)
  await register(seller.page, 'Vendedor Chat', seller.email, 'Recife', /Crie sua conta/)
  await listItem(seller.page, title)
  const listingUrl = seller.page.url()

  const buyer = await newUser(browser, 'en', info.project.name)
  await register(buyer.page, 'Buyer Chat', buyer.email, 'Olinda', /Create your account/)
  await buyer.page.goto(listingUrl)
  await buyer.page.getByRole('button', { name: 'Is it still available?' }).click()
  await expect(buyer.page).toHaveURL(/\/inbox\//)
  await expect(buyer.page.getByText('Is it still available?')).toBeVisible()

  // The seller sees the new conversation and answers.
  await seller.page.goto('/inbox')
  await seller.page.getByRole('link', { name: /Buyer Chat/ }).click()
  await seller.page.getByLabel('Mensagem').fill('Sim, está disponível!')
  await seller.page.getByRole('button', { name: 'Enviar' }).click()
  // Delivered to the buyer's open screen by the stream, well before the 30 s fallback poll.
  await expect(buyer.page.getByText('Sim, está disponível!')).toBeVisible({ timeout: 15_000 })

  // Offer 1.000,00, counter 1.100,00, accept.
  await buyer.page.getByRole('button', { name: 'Make an offer' }).click()
  await buyer.page.getByLabel('Your price').fill('1.000,00')
  await buyer.page.getByRole('button', { name: 'Send offer' }).click()
  await expect(seller.page.getByText('R$ 1.000,00')).toBeVisible({ timeout: 15_000 })
  await seller.page.getByRole('button', { name: 'Contrapropor' }).click()
  await seller.page.getByLabel('Seu preço').fill('1.100,00')
  await seller.page.getByRole('button', { name: 'Enviar oferta' }).click()
  // The English interface formats money for its language: R$1,100.00.
  await expect(buyer.page.getByText(/R\$\s?1,100\.00/)).toBeVisible({ timeout: 15_000 })
  const axe = await new AxeBuilder({ page: buyer.page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  expect(axe.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => v.id)).toEqual([])
  await buyer.page.getByRole('button', { name: 'Accept' }).click()

  // The buyer pays the agreed price through the usual checkout.
  await buyer.page.getByRole('button', { name: /Pay R\$\s?1,100\.00/ }).click()
  await expect(buyer.page.getByText(/Price agreed in the chat: R\$\s?1,100\.00/)).toBeVisible()
  await buyer.page.getByRole('button', { name: /Continue to payment/ }).click()
  await expect(buyer.page.getByText(/R\$\s?1,130\.00/).first()).toBeVisible() // 1,100.00 + 30.00 shipping
  await buyer.page.getByRole('button', { name: 'Pay with a test card' }).click()
  await expect(buyer.page.getByRole('heading', { name: 'Payment confirmed' })).toBeVisible()

  await seller.page.goto('/notifications')
  await expect(seller.page.getByText(/Você vendeu/)).toBeVisible()
})

test('public questions are answered by the seller', async ({ browser }, info) => {
  const title = `Bicicleta pergunta ${unique()}`
  const seller = await newUser(browser, 'pt-BR', info.project.name)
  await register(seller.page, 'Vendedora Perguntas', seller.email, 'Recife', /Crie sua conta/)
  await listItem(seller.page, title)
  const listingUrl = seller.page.url()

  const buyer = await newUser(browser, 'en', info.project.name)
  await register(buyer.page, 'Ana Compradora', buyer.email, 'Olinda', /Create your account/)
  await buyer.page.goto(listingUrl)
  await buyer.page.getByLabel('Your question').fill('Does it come with the receipt?')
  await buyer.page.getByRole('button', { name: 'Ask', exact: true }).click()
  await expect(buyer.page.getByText("Waiting for the seller's answer.")).toBeVisible()

  await seller.page.goto(listingUrl)
  await expect(seller.page.getByText('1 sem resposta')).toBeVisible()
  await seller.page.getByLabel('Sua resposta').fill('Sim, com nota fiscal.')
  await seller.page.getByRole('button', { name: 'Responder' }).click()
  await expect(seller.page.getByText('Sim, com nota fiscal.')).toBeVisible()

  const visitor = await browser.newContext({ ...info.project.use, locale: 'en' })
  const page = await visitor.newPage()
  await page.goto(listingUrl)
  await expect(page.getByText('Does it come with the receipt?')).toBeVisible()
  await expect(page.getByText('Sim, com nota fiscal.')).toBeVisible()
  await expect(page.getByText(/· Ana ·/)).toBeVisible() // first name only
  const axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  expect(axe.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => v.id)).toEqual([])

  // The seller's reputation explains itself.
  await page.getByRole('link', { name: /Vendedora Perguntas/ }).first().click()
  await page.getByRole('link', { name: 'How it is calculated' }).click()
  await expect(page.getByRole('heading', { name: 'How reputation works' })).toBeVisible()
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
