import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { TFunction } from 'i18next'
import { api, json } from './client'
import type { ListingCard } from './types'

export function useSimilarListings(listingId: string) {
  return useQuery({
    queryKey: ['similar', listingId],
    queryFn: () => api<ListingCard[]>(`/api/listings/${listingId}/similar`),
    staleTime: 5 * 60_000,
  })
}

export function useSuggestions(q: string, enabled: boolean) {
  return useQuery({
    queryKey: ['suggest', q],
    queryFn: () => api<{ queries: string[] }>(`/api/search/suggest?q=${encodeURIComponent(q)}`),
    enabled: enabled && q.length >= 2,
    staleTime: 30_000,
    placeholderData: (previous) => previous,
  })
}

/** The search fields a saved search keeps (distance from a city is not one of them). */
export const SAVED_FIELDS = ['q', 'category', 'condition', 'state', 'min_price', 'max_price'] as const
export type SavedParams = Partial<Record<(typeof SAVED_FIELDS)[number], string | number>>

export interface SavedSearch {
  id: string
  params: SavedParams
  created_at: string
}

/** The saved form of the current search, or null when it is not specific enough to follow. */
export function savedParamsOf(search: URLSearchParams): SavedParams | null {
  const out: SavedParams = {}
  for (const field of SAVED_FIELDS) {
    const value = search.get(field)?.trim()
    if (!value) continue
    out[field] = field === 'min_price' || field === 'max_price' ? Number(value) : value.replace(/\s+/g, ' ')
  }
  return out.q || out.category ? out : null
}

/** Same comparison the server uses to keep one copy of a search. */
export function sameSearch(a: SavedParams, b: SavedParams): boolean {
  const norm = (p: SavedParams) =>
    JSON.stringify(SAVED_FIELDS.map((f) => (f === 'q' ? String(p.q ?? '').toLowerCase() : p[f] ?? null)))
  return norm(a) === norm(b)
}

/** Short name of a saved search: its words, its category, or both. */
export function savedSearchLabel(params: SavedParams, t: TFunction): string {
  const category = params.category
    ? t(`categories.${params.category}`, { defaultValue: String(params.category) })
    : ''
  if (params.q && category) return `“${params.q}” · ${category}`
  return params.q ? `“${params.q}”` : category
}

export function searchPath(params: SavedParams): string {
  const query = new URLSearchParams()
  for (const field of SAVED_FIELDS) if (params[field] !== undefined) query.set(field, String(params[field]))
  return `/search?${query}`
}

export function useSavedSearches(enabled = true) {
  return useQuery({
    queryKey: ['saved-searches'],
    queryFn: () => api<SavedSearch[]>('/api/me/saved-searches'),
    enabled,
  })
}

export function useSaveSearch() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (params: SavedParams) => api<SavedSearch>('/api/me/saved-searches', { method: 'POST', body: json(params) }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['saved-searches'] }),
  })
}

export function useDeleteSavedSearch() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api<null>(`/api/me/saved-searches/${id}`, { method: 'DELETE' }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['saved-searches'] }),
  })
}
