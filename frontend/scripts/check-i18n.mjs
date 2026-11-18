// Fails when a translation key exists in one language and not in the other.
import { readFileSync } from 'node:fs'

const load = (name) => JSON.parse(readFileSync(new URL(`../src/i18n/locales/${name}.json`, import.meta.url)))
const flatten = (obj, prefix = '') =>
  Object.entries(obj).flatMap(([k, v]) =>
    v && typeof v === 'object' ? flatten(v, `${prefix}${k}.`) : [`${prefix}${k}`],
  )
const languages = { 'pt-BR': load('pt-BR'), en: load('en') }
const keys = Object.fromEntries(Object.entries(languages).map(([l, d]) => [l, new Set(flatten(d))]))
let missing = 0
for (const [lang, set] of Object.entries(keys)) {
  for (const [other, otherSet] of Object.entries(keys)) {
    if (lang === other) continue
    for (const key of otherSet) {
      if (!set.has(key)) {
        console.error(`${lang} is missing "${key}" (present in ${other})`)
        missing++
      }
    }
  }
}
if (missing) process.exit(1)
console.log(`i18n ok: ${keys.en.size} keys in each language`)
