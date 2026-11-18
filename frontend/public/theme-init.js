// Applies the dark theme before first paint (no flash). Mirrors src/theme.ts. Kept as a file:
// the Content-Security-Policy allows no inline scripts.
try {
  var t = localStorage.getItem('tp_theme')
  if (t === 'dark' || (t !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches))
    document.documentElement.classList.add('dark')
} catch (e) {}
