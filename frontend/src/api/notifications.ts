import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { api, json } from './client'
import { searchPath, type SavedParams } from './discovery'

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
    // Something changed for this user (a chat message, a read mark): open chat screens refetch.
    source.addEventListener('wake', () => {
      void qc.invalidateQueries({ queryKey: ['conversations'] })
      void qc.invalidateQueries({ queryKey: ['messages'] })
    })
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
  if (n.payload.conversation_id) return `/inbox/${String(n.payload.conversation_id)}`
  if (n.payload.dispute_id) return `/disputes/${String(n.payload.dispute_id)}`
  if (n.kind === 'ops_alert') return '/admin/ops'
  if (n.kind === 'saved_search' && n.payload.params) {
    // Newest first: the alert is about listings published since the last check.
    return `${searchPath(n.payload.params as SavedParams)}&sort=newest`
  }
  if (n.kind === 'new_follower' && n.payload.user_id) return `/users/${String(n.payload.user_id)}`
  if (n.kind === 'listing_removed') return '/me/listings'
  if (n.payload.listing_id && !n.payload.order_id) return `/listings/${String(n.payload.listing_id)}`
  if (SELLER_KINDS.has(n.kind)) return '/seller'
  if (n.payload.order_id) return '/orders'
  return '/'
}

