import { useInfiniteQuery } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { api } from './client'
import type { ListingPage } from './types'

export function useListingSearch(filters: Record<string, string>) {
  return useInfiniteQuery({
    queryKey: ['listings', filters],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => {
      const query = new URLSearchParams(filters)
      if (pageParam) query.set('cursor', pageParam)
      return api<ListingPage>(`/api/listings?${query}`)
    },
    getNextPageParam: (last) => last.next_cursor,
  })
}

/** Calls `onVisible` when the returned element scrolls into view (infinite scrolling). */
export function useOnVisible(onVisible: () => void, enabled: boolean) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el || !enabled) return
    const observer = new IntersectionObserver(
      (entries) => entries.some((e) => e.isIntersecting) && onVisible(),
      { rootMargin: '600px' },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [onVisible, enabled])
  return ref
}
