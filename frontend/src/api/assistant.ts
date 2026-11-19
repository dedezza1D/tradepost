import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, json } from './client'
import type { Condition } from './types'

export interface Draft {
  title: string
  description: string
  category_slug: string | null
  condition: Condition
  brand: string
  color: string
}

export interface PriceSuggestion {
  low_cents: number
  high_cents: number
  median_cents: number
  count: number
  basis: 'similar' | 'category'
}

export type TipCode =
  | 'few_photos'
  | 'dark_photo'
  | 'short_title'
  | 'shouting_title'
  | 'short_description'
  | 'price_above_similar'

export interface Tip {
  code: TipCode
  data: Record<string, number>
}

/** Whether a model is available for drafts (the live demo runs without one). */
export function useAssistStatus(enabled: boolean) {
  return useQuery({
    queryKey: ['assist-status'],
    queryFn: () => api<{ draft: boolean }>('/api/assist/status'),
    enabled,
    staleTime: 5 * 60_000,
  })
}

export const DRAFT_PHOTOS = 3

export function useDraftFromPhotos() {
  return useMutation({
    mutationFn: (files: File[]) => {
      const body = new FormData()
      for (const file of files.slice(0, DRAFT_PHOTOS)) body.append('files', file)
      return api<Draft>('/api/assist/draft', { method: 'POST', body })
    },
  })
}

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms)
    return () => clearTimeout(id)
  }, [value, ms])
  return debounced
}

/** A price range from similar listings, refreshed as the title and category change. */
export function usePriceSuggestion(title: string, description: string, category: string) {
  const body = useDebounced({ title: title.trim(), description: description.trim().slice(0, 500), category_slug: category || null }, 400)
  return useQuery({
    queryKey: ['price-suggestion', body],
    queryFn: () => api<PriceSuggestion | null>('/api/assist/price', { method: 'POST', body: json(body) }),
    enabled: body.title.length >= 2,
    staleTime: 5 * 60_000,
  })
}

export function useListingTips(listingId: string, revision: string | undefined) {
  return useQuery({
    queryKey: ['listing-tips', listingId, revision],
    queryFn: () => api<Tip[]>(`/api/assist/listings/${listingId}/tips`),
    enabled: revision !== undefined,
  })
}
