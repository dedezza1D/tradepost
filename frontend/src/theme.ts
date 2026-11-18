export type ThemeChoice = 'system' | 'light' | 'dark'

const KEY = 'tp_theme'
const media = window.matchMedia('(prefers-color-scheme: dark)')

export function themeChoice(): ThemeChoice {
  try {
    const stored = localStorage.getItem(KEY)
    return stored === 'light' || stored === 'dark' ? stored : 'system'
  } catch {
    return 'system'
  }
}

function apply(choice: ThemeChoice) {
  const dark = choice === 'dark' || (choice === 'system' && media.matches)
  document.documentElement.classList.toggle('dark', dark)
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute('content', dark ? '#0c1311' : '#f5f6f3')
}

export function setThemeChoice(choice: ThemeChoice) {
  try {
    if (choice === 'system') localStorage.removeItem(KEY)
    else localStorage.setItem(KEY, choice)
  } catch {
    // Not persisted in this browser; still applied now.
  }
  apply(choice)
}

media.addEventListener('change', () => apply(themeChoice()))
apply(themeChoice())
