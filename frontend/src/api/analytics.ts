import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from './client'

export const METRICS = ['views', 'saves', 'questions', 'conversations', 'orders', 'revenue_cents'] as const
export type Metric = (typeof METRICS)[number]

export type DayPoint = { date: string } & Record<Metric, number>

export interface ListingRow extends Record<Metric, number> {
  listing_id: string
  title: string
  status: string
  units: number
  /** Orders per view; null when nobody saw the listing in the period. */
  conversion: number | null
}

export interface SellerAnalytics {
  start: string
  end: string
  totals: Record<Metric, number> & { refunds: number; conversion: number | null }
  series: DayPoint[]
  listings: ListingRow[]
  response: { conversations: number; answered: number; median_minutes: number | null }
  coupons: {
    orders_with_coupon: number
    discount_cents: number
    revenue_with_coupon_cents: number
    revenue_without_coupon_cents: number
    by_code: { code: string; orders: number; discount_cents: number; revenue_cents: number }[]
  }
}

export const PERIODS = [7, 30, 90] as const

export function useSellerAnalytics(days: number) {
  return useQuery({
    queryKey: ['seller-analytics', days],
    queryFn: () => api<SellerAnalytics>(`/api/me/analytics?days=${days}`),
    placeholderData: keepPreviousData,
  })
}

export const analyticsCsvUrl = (days: number) => `/api/me/analytics/export.csv?days=${days}`
