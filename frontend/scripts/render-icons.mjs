// Renders the app icons (PWA, Apple touch) from the brand mark. Run after changing the mark:
//   node scripts/render-icons.mjs
import { chromium } from '@playwright/test'
import { readFile } from 'node:fs/promises'

const mark = await readFile(new URL('../public/favicon.svg', import.meta.url), 'utf8')
// The maskable icon fills the whole square (the platform crops it), with the tag inside the
// safe zone: a red background and the mark's tag scaled to 60%.
const tag = mark.replace(/<rect[^>]*\/>/, '').replace(/<defs>.*<\/defs>/, '')
const maskable = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" fill="#d41b2e"/><g transform="translate(16 16) scale(0.6) translate(-16 -16)">${tag.replace(/^<svg[^>]*>|<\/svg>\s*$/g, '')}</g></svg>`

const outputs = [
  { file: 'pwa-192.png', size: 192, svg: mark },
  { file: 'pwa-512.png', size: 512, svg: mark },
  { file: 'apple-touch-icon.png', size: 180, svg: maskable },
  { file: 'pwa-maskable-512.png', size: 512, svg: maskable },
]

const browser = await chromium.launch()
const page = await browser.newPage()
for (const { file, size, svg } of outputs) {
  await page.setViewportSize({ width: size, height: size })
  await page.setContent(
    `<html><body style="margin:0;background:transparent">${svg.replace('<svg ', `<svg width="${size}" height="${size}" `)}</body></html>`,
  )
  await page.screenshot({ path: new URL(`../public/${file}`, import.meta.url).pathname.replace(/^\/([A-Z]:)/, '$1'), omitBackground: true })
  console.log('wrote', file)
}
await browser.close()
