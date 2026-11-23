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
  // The city picker inside the page (the header search box is also a combobox).
  await page.getByRole('main').getByRole('combobox').first().fill(city)
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
  // The home also shows the newest listings in a rail; the Following feed is its own panel.
  await expect(buyer.page.locator('#feed-panel').getByRole('link', { name: new RegExp(title) })).toBeVisible()
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

  // Performance: the sale and the save, read from the orders and saves themselves.
  const performance = seller.page.getByRole('region', { name: 'Desempenho' })
  const tile = (name: string) => performance.getByRole('button', { name: new RegExp(`^${name}`) }).locator(':scope > span').nth(1)
  await expect(tile('Pedidos')).toHaveText('1')
  await expect(tile('Salvos')).toHaveText('1')
  await expect(performance.getByRole('link', { name: title })).toBeVisible()
  await performance.getByRole('button', { name: 'Ver como tabela' }).click()
  await expect(performance.getByRole('columnheader', { name: 'Dia', exact: true })).toBeVisible()
  const axe = await new AxeBuilder({ page: seller.page }).include('section[aria-labelledby="analytics-title"]')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  expect(axe.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => v.id)).toEqual([])
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
  await seller.page.getByRole('button', { name: 'Enviar', exact: true }).click()
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
  // On phones this button sits low on the page: it must not end up under the fixed bars.
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

test('an item with options and shipping is bought with a coupon in installments, then disputed and refunded', async ({ browser }, info) => {
  test.setTimeout(180_000)
  const title = `Camiseta e2e ${unique()}`
  const seller = await newUser(browser, 'pt-BR', info.project.name)
  await register(seller.page, 'Loja Opções', seller.email, 'Recife', /Crie sua conta/)
  const s = seller.page
  await s.goto('/sell')
  await s.locator('input[type=file]').setInputFiles({ name: 'shirt.png', mimeType: 'image/png', buffer: png() })
  await s.getByRole('button', { name: 'Continuar' }).click()
  await s.locator('#title').fill(title)
  await s.locator('#description').fill('Camiseta de algodão, várias medidas.')
  await s.locator('#category').selectOption('fashion')
  await s.getByRole('button', { name: 'Continuar' }).click()
  await s.locator('#price').fill('80,00')
  await s.getByRole('button', { name: 'Adicionar opção' }).click()
  await s.locator('#variant-name-0').fill('M')
  await s.locator('#variant-stock-0').fill('2')
  await s.getByRole('button', { name: 'Adicionar opção' }).click()
  await s.locator('#variant-name-1').fill('G')
  await s.locator('#variant-stock-1').fill('1')
  await s.getByText('Calcular pelo destino').click()
  await s.locator('#weight').fill('0,4')
  await s.locator('#length').fill('30')
  await s.locator('#width').fill('25')
  await s.locator('#height').fill('5')
  await s.getByRole('button', { name: 'Continuar' }).click()
  await s.getByRole('button', { name: 'Publicar' }).click()
  await expect(s.getByRole('heading', { name: title })).toBeVisible()
  const listingUrl = s.url()

  // A 10% coupon from this seller.
  await s.goto('/seller')
  await s.getByLabel('Código').fill('CAMISA10')
  await s.getByLabel('Desconto (%)').fill('10')
  await s.getByRole('button', { name: 'Criar cupom' }).click()
  await expect(s.getByText('CAMISA10', { exact: true })).toBeVisible()

  const buyer = await newUser(browser, 'en', info.project.name)
  await register(buyer.page, 'Buyer Options', buyer.email, 'Porto Alegre', /Create your account/)
  const b = buyer.page
  await b.goto(listingUrl)
  // Buying needs an option first.
  await b.locator('button:visible', { hasText: /Buy (now|for)/ }).first().click()
  await expect(b.getByText('Choose an option (size, color…) first.').first()).toBeVisible()
  await b.getByText('G', { exact: true }).click()
  // The shipping quote uses the buyer's city.
  await expect(b.getByText('Express').first()).toBeVisible()
  await b.locator('button:visible', { hasText: /Buy (now|for)/ }).first().click()

  await expect(b.getByRole('heading', { name: 'Checkout' })).toBeVisible()
  await expect(b.getByText(`1 × ${title} (G)`)).toBeVisible()
  await b.getByText('Express', { exact: true }).click()
  await b.getByLabel('Have a coupon?').fill('camisa10')
  await b.getByRole('button', { name: 'Apply' }).click()
  await expect(b.getByText('Coupon camisa10')).toBeVisible()
  await expect(b.getByText(/R\$\s?8\.00/).first()).toBeVisible() // 10% of 80.00
  await b.getByRole('button', { name: /Continue to payment/ }).click()
  await b.getByLabel('Installments').selectOption('3')
  await b.getByRole('button', { name: 'Pay with a test card' }).click()
  await expect(b.getByRole('heading', { name: 'Payment confirmed' })).toBeVisible()

  await b.goto('/orders')
  await expect(b.getByText(`1 × ${title} (G)`)).toBeVisible()
  await expect(b.getByText(/Express · \d+ business days? · Porto Alegre - RS/)).toBeVisible()
  await expect(b.getByText('in 3 installments')).toBeVisible()
  await expect(b.getByText(/saved R\$\s?8\.00/)).toBeVisible()

  // Shipped, the shirt arrives damaged: the buyer disputes and the seller refunds it all.
  await s.goto('/seller')
  await s.getByPlaceholder('Código de rastreio').fill('BR987654321BR')
  await s.getByRole('button', { name: 'Marcar enviado' }).click()
  await expect(s.getByText('BR987654321BR')).toBeVisible()
  await b.goto('/orders')
  await b.getByRole('button', { name: 'Problem with this order?' }).click()
  await b.getByText('Arrived damaged or faulty').click()
  await b.getByLabel('Tell what happened').fill('The shirt came torn.')
  await b.getByRole('button', { name: 'Open dispute', exact: true }).click()
  await expect(b.getByRole('heading', { name: 'Dispute' })).toBeVisible()
  await expect(b.getByText('With the seller', { exact: true })).toBeVisible()

  await s.goto('/seller')
  await s.getByRole('link', { name: /Ver disputa/ }).click()
  await expect(s.getByText('The shirt came torn.')).toBeVisible()
  await s.getByRole('button', { name: 'Reembolsar' }).click()
  await expect(s.getByText('Reembolsada').first()).toBeVisible()
  await b.reload()
  await expect(b.getByText('Refunded', { exact: true })).toBeVisible()
  // The G option is sold out; M still has stock.
  await b.goto(listingUrl)
  await expect(b.locator('input[name=variant]').nth(1)).toBeDisabled()
  await expect(b.locator('input[name=variant]').nth(0)).toBeEnabled()
})

test('notification preferences are kept per kind of notice', async ({ browser }, info) => {
  const user = await newUser(browser, 'en', info.project.name)
  await register(user.page, 'Prefs E2E', user.email, 'Recife', /Create your account/)
  await user.page.goto('/me#notifications')
  const section = user.page.getByRole('region', { name: 'Notifications outside the app' })
  // The test server has no VAPID key (its e-mails go to the log): choices are still saved.
  await expect(section.getByText('Push notifications are not set up on this server.')).toBeVisible()
  await expect(section.getByRole('checkbox', { name: 'E-mail for Orders and disputes' })).toBeChecked()
  const offers = section.getByRole('checkbox', { name: 'Push for Offers' })
  await expect(offers).toBeChecked()
  await offers.uncheck()
  await expect(offers).not.toBeChecked()
  await user.page.reload()
  await expect(section.getByRole('checkbox', { name: 'Push for Offers' })).not.toBeChecked()
  const axe = await new AxeBuilder({ page: user.page }).include('#notifications')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  expect(axe.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => v.id)).toEqual([])
})

test('a forgotten password is asked for by e-mail without revealing accounts', async ({ browser }, info) => {
  const user = await newUser(browser, 'pt-BR', info.project.name)
  const { page } = user
  await page.goto('/login')
  await page.locator('#email').fill(user.email)
  await page.getByRole('link', { name: 'Esqueci minha senha' }).click()
  await expect(page.getByRole('heading', { name: 'Redefinir a senha' })).toBeVisible()
  // The address typed on the sign-in page comes along. Nobody has it: the answer is the same.
  await expect(page.locator('#email')).toHaveValue(user.email)
  await page.getByRole('button', { name: 'Enviar link' }).click()
  await expect(page.getByRole('heading', { name: 'Confira seu e-mail' })).toBeVisible()
  await expect(page.getByRole('main').getByRole('status')).toContainText(`Se houver uma conta com ${user.email}`)

  // A link that is unknown, used or expired leads back to asking for a new one.
  await page.goto(`/reset-password#token=${'x'.repeat(43)}`)
  await expect(page.getByRole('heading', { name: 'Crie uma senha nova' })).toBeVisible()
  await page.locator('#new_password').fill('uma senha nova e bem comprida')
  await page.getByRole('button', { name: 'Salvar senha nova' }).click()
  await expect(page.getByRole('main').getByRole('alert')).toContainText('Este link expirou')
  await page.getByRole('link', { name: 'Pedir outro link' }).click()
  await expect(page).toHaveURL('/forgot-password')

  await page.goto('/reset-password')
  await expect(page.getByRole('main').getByRole('alert')).toContainText('Este link está incompleto')
})

test('main pages have no serious accessibility violations', async ({ page }) => {
  for (const path of ['/', '/search', '/login', '/register', '/forgot-password', '/reset-password#token=e2e-token-that-is-long-enough']) {
    await page.goto(path)
    await page.waitForLoadState('networkidle')
    const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
    const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
    expect(serious.map((v) => `${path}: ${v.id} (${v.nodes.length})`)).toEqual([])
  }
})
