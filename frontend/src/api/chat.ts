import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMe } from './auth'
import { api, json } from './client'
import type { Checkout } from './orders'
import type { ChatMessage, Conversation, Offer } from './types'

interface Inbox {
  items: Conversation[]
  next_before: string | null
  unread_total: number
}

interface MessagePage {
  items: ChatMessage[]
  has_older: boolean
}

// The notification stream sends "wake" when something changes for the user; these queries
// refetch then. The intervals are only a safety net for when the stream is down.
const SAFETY_POLL_MS = 30_000

export function useInbox() {
  return useInfiniteQuery({
    queryKey: ['conversations'],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => api<Inbox>(`/api/conversations${pageParam ? `?before=${encodeURIComponent(pageParam)}` : ''}`),
    getNextPageParam: (last) => last.next_before,
    refetchInterval: SAFETY_POLL_MS,
  })
}

export function useUnreadChats() {
  const { data: me } = useMe()
  return useQuery({
    queryKey: ['conversations', 'unread'],
    queryFn: () => api<{ count: number }>('/api/conversations/unread'),
    enabled: Boolean(me),
    refetchInterval: SAFETY_POLL_MS,
    select: (d) => d.count,
  })
}

export function useConversation(id: string) {
  return useQuery({
    queryKey: ['conversations', 'one', id],
    queryFn: () => api<Conversation>(`/api/conversations/${id}`),
    refetchInterval: SAFETY_POLL_MS,
  })
}

/** Messages in chronological order; older pages load on demand. */
export function useMessages(id: string) {
  return useInfiniteQuery({
    queryKey: ['messages', id],
    initialPageParam: null as number | null,
    queryFn: ({ pageParam }) => api<MessagePage>(`/api/conversations/${id}/messages${pageParam ? `?before=${pageParam}` : ''}`),
    getNextPageParam: (last) => (last.has_older && last.items.length ? last.items[0].id : null),
    refetchInterval: SAFETY_POLL_MS,
  })
}

function useRefreshChat(id?: string) {
  const qc = useQueryClient()
  return () => {
    void qc.invalidateQueries({ queryKey: ['conversations'] })
    if (id) void qc.invalidateQueries({ queryKey: ['messages', id] })
  }
}

export function useStartConversation() {
  const refresh = useRefreshChat()
  return useMutation({
    mutationFn: (body: { listing_id: string; body?: string }) =>
      api<Conversation>('/api/conversations', { method: 'POST', body: json(body) }),
    onSuccess: refresh,
  })
}

export function useSendMessage(id: string) {
  const refresh = useRefreshChat(id)
  return useMutation({
    mutationFn: (body: string) => api<ChatMessage>(`/api/conversations/${id}/messages`, { method: 'POST', body: json({ body }) }),
    onSuccess: refresh,
  })
}

export function useMarkRead(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api<void>(`/api/conversations/${id}/read`, { method: 'POST' }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['conversations'] })
      void qc.invalidateQueries({ queryKey: ['notifications'] })
    },
  })
}

export type OfferAction = 'accept' | 'decline' | 'cancel' | 'counter'

export function useOfferActions(conversationId: string) {
  const refresh = useRefreshChat(conversationId)
  const make = useMutation({
    mutationFn: (amount_cents: number) =>
      api<Offer>(`/api/conversations/${conversationId}/offers`, { method: 'POST', body: json({ amount_cents }) }),
    onSuccess: refresh,
  })
  const respond = useMutation({
    mutationFn: ({ offerId, action, amount_cents }: { offerId: string; action: OfferAction; amount_cents?: number }) =>
      api<Offer>(`/api/offers/${offerId}/${action}`, {
        method: 'POST',
        body: action === 'counter' ? json({ amount_cents }) : undefined,
      }),
    onSuccess: refresh,
  })
  return { make, respond }
}

export function usePayOffer() {
  return useMutation({
    mutationFn: ({ offerId, provider, key }: { offerId: string; provider: string; key: string }) =>
      api<Checkout>(`/api/offers/${offerId}/checkout`, {
        method: 'POST',
        headers: { 'Idempotency-Key': key },
        body: json({ provider }),
      }),
  })
}
