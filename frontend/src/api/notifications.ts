import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { api, json } from './client'

export interface Notification {
  id: number
  kind: string
  payload: Record<string, unknown>
  created_at: string
  read_at: string | null
}

export interface NotificationPage {
  items: Notification[]
  unread: number
  next_before: number | null
}

export const NOTIFICATIONS_KEY = ['notifications'] as const

export const fetchNotifications = () => api<NotificationPage>('/api/notifications')
export const markAllRead = () =>
  api<{ unread: number }>('/api/notifications/read', { method: 'POST', body: json({ all: true }) })

const POLL_MS = 30_000

/**
 * Keeps the notifications query live while signed in.
 *
 * Server-sent events push new notifications as they happen; EventSource reconnects on its own
 * and resumes from the last event id. If the stream keeps failing (a proxy that buffers, the API
 * waking up), it falls back to polling, so nothing depends on the stream working.
 */
export function useNotificationStream(enabled: boolean) {
  const qc = useQueryClient()

  useEffect(() => {
    if (!enabled) return
    const refresh = () => qc.invalidateQueries({ queryKey: NOTIFICATIONS_KEY })
    let failures = 0
    let poll: number | undefined
    const source = new EventSource('/api/stream', { withCredentials: true })

    const startPolling = () => {
      if (poll === undefined) poll = window.setInterval(refresh, POLL_MS)
    }
    source.addEventListener('open', () => {
      failures = 0
      if (poll !== undefined) {
        window.clearInterval(poll)
        poll = undefined
      }
    })
    source.addEventListener('notification', refresh)
    source.addEventListener('unread', (event) => {
      const { count } = JSON.parse((event as MessageEvent).data) as { count: number }
      qc.setQueryData<NotificationPage>(NOTIFICATIONS_KEY, (page) =>
        page ? { ...page, unread: count } : page,
      )
    })
    source.addEventListener('error', () => {
      failures += 1
      if (failures >= 3) startPolling()
    })
    return () => {
      source.close()
      if (poll !== undefined) window.clearInterval(poll)
    }
  }, [enabled, qc])
}

const SELLER_KINDS = new Set(['order_paid', 'order_delivered', 'payout_sent', 'review_received'])

/** Where tapping a notification goes. */
export function notificationTarget(n: Notification): string {
  if (SELLER_KINDS.has(n.kind)) return '/seller'
  if (n.payload.order_id) return '/orders'
  return '/'
}

