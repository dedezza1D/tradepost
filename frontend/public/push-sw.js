// Push handlers, loaded by the generated service worker (vite.config.ts: workbox.importScripts).
// The server sends { title, body, url, tag }, already written in the reader's language.

self.addEventListener('push', (event) => {
  let data = {}
  try {
    data = event.data ? event.data.json() : {}
  } catch {
    data = { body: event.data ? event.data.text() : '' }
  }
  event.waitUntil(
    self.registration.showNotification(data.title || 'tradepost', {
      body: data.body || '',
      tag: data.tag || undefined,
      // A newer notice for the same conversation or order replaces the old one, and still alerts.
      renotify: Boolean(data.tag),
      icon: '/pwa-192.png',
      data: { url: typeof data.url === 'string' ? data.url : '/' },
    }),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  let target = new URL(event.notification.data?.url || '/', self.location.origin)
  // Only pages of this site open from a notice.
  if (target.origin !== self.location.origin) target = new URL('/', self.location.origin)
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(async (windows) => {
      for (const client of windows) {
        if (new URL(client.url).origin === self.location.origin && 'focus' in client) {
          await client.focus()
          return client.navigate(target.href)
        }
      }
      return self.clients.openWindow(target.href)
    }),
  )
})
