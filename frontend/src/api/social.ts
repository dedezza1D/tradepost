import { useInfiniteQuery, useMutation, useQueryClient, type InfiniteData, type QueryClient } from '@tanstack/react-query'
import { api, json } from './client'
import type { FeedTab, Listing, ListingPage, Profile, ReportReason, UserSummary } from './types'

function infinite<T extends { next_cursor: string | null }>(key: unknown[], path: string, params: Record<string, string> = {}) {
  return {
    queryKey: key,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }: { pageParam: string | null }) => {
      const query = new URLSearchParams(params)
      if (pageParam) query.set('cursor', pageParam)
      return api<T>(`${path}?${query}`)
    },
    getNextPageParam: (last: T) => last.next_cursor,
  }
}

export function useFeed(tab: FeedTab, params: Record<string, string> = {}, enabled = true) {
  return useInfiniteQuery({ ...infinite<ListingPage>(['feed', tab, params], '/api/feed', { tab, ...params }), enabled })
}

export function useUserListings(userId: string, enabled = true) {
  return useInfiniteQuery({ ...infinite<ListingPage>(['user-listings', userId], `/api/users/${userId}/listings`), enabled })
}

export function useSavedListings() {
  return useInfiniteQuery(infinite<ListingPage>(['saved'], '/api/me/saved'))
}

export function useFollowing() {
  return useInfiniteQuery(infinite<{ items: UserSummary[]; next_cursor: string | null }>(['following'], '/api/me/following'))
}

/** Updates a listing's saved state in every cached list and page that shows it. */
function patchListing(qc: QueryClient, id: string, saved: boolean, saveCount: number) {
  const patchPages = (data: InfiniteData<ListingPage> | undefined) =>
    data && {
      ...data,
      pages: data.pages.map((page) => ({
        ...page,
        items: page.items.map((item) => (item.id === id ? { ...item, saved, save_count: saveCount } : item)),
      })),
    }
  for (const key of ['listings', 'feed', 'user-listings']) {
    qc.setQueriesData<InfiniteData<ListingPage>>({ queryKey: [key] }, patchPages)
  }
  qc.setQueryData<Listing>(['listing', id], (listing) => listing && { ...listing, saved, save_count: saveCount })
}

export function useToggleSave() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, save }: { id: string; save: boolean }) =>
      api<{ saved: boolean; save_count: number }>(`/api/listings/${id}/save`, { method: save ? 'PUT' : 'DELETE' }),
    onSuccess: (state, { id }) => {
      patchListing(qc, id, state.saved, state.save_count)
      void qc.invalidateQueries({ queryKey: ['saved'] })
    },
  })
}

export function useToggleFollow(userId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (follow: boolean) =>
      api<{ following: boolean; follower_count: number }>(`/api/users/${userId}/follow`, { method: follow ? 'PUT' : 'DELETE' }),
    onSuccess: (state) => {
      qc.setQueryData<Profile>(['profile', userId], (p) => p && { ...p, following: state.following, follower_count: state.follower_count })
      void qc.invalidateQueries({ queryKey: ['following'] })
      void qc.invalidateQueries({ queryKey: ['feed', 'following'] })
    },
  })
}

export function useToggleBlock(userId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (block: boolean) => api<void>(`/api/users/${userId}/block`, { method: block ? 'PUT' : 'DELETE' }),
    // Blocking hides content across the app: refetch everything that lists users or listings.
    onSuccess: () => void qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'me' }),
  })
}

export function useReport() {
  return useMutation({
    mutationFn: (body: { target_type: 'listing' | 'user'; target_id: string; reason: ReportReason; details?: string }) =>
      api<{ id: string }>('/api/reports', { method: 'POST', body: json(body) }),
  })
}
