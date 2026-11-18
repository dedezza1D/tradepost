import { useQuery } from '@tanstack/react-query'
import { api } from './client'
import type { ListingStatus } from './types'

export type CheckoutStatus = 'pending' | 'paid' | 'expired' | 'cancelled'
export type OrderStatus =
  | 'awaiting_payment'
  | 'paid'
  | 'shipped'
  | 'delivered'
  | 'cancelled'
  | 'refunded'
export type AttemptStatus = 'pending' | 'succeeded' | 'failed' | 'cancelled' | 'refunded'

export interface CartLine {
  listing_id: string
  variant_id: string | null
  variant_name: string | null
  title: string
  price_cents: number
  shipping_cents: number
  quantity: number
  available: number
  status: ListingStatus
  thumb_url: string | null
  seller_id: string
  seller_name: string
}

export interface Cart {
  items: CartLine[]
  items_cents: number
  shipping_cents: number
  total_cents: number
}

export interface Party {
  id: string
  display_name: string
}

export interface OrderItem {
  listing_id: string
  title: string
  variant_name: string | null
  unit_price_cents: number
  quantity: number
  shipping_cents: number
  thumb_url: string | null
}

export interface OrderEvent {
  from_status: string | null
  to_status: string
  actor: string
  note: string | null
  created_at: string
}

export interface Order {
  id: string
  checkout_id: string
  status: OrderStatus
  buyer: Party
  seller: Party
  items: OrderItem[]
  items_cents: number
  shipping_cents: number
  total_cents: number
  platform_fee_cents: number | null
  seller_net_cents: number | null
  tracking_code: string | null
  shipping_service: string | null
  shipping_days: number | null
  ship_to: string | null
  discount_cents: number
  installments: number | null
  dispute_id: string | null
  dispute_status: 'open' | 'seller_rejected' | 'escalated' | 'refunded' | 'rejected' | 'cancelled' | null
  created_at: string
  paid_at: string | null
  shipped_at: string | null
  delivered_at: string | null
  events: OrderEvent[]
  reviewed: boolean
}

export interface Attempt {
  id: string
  method: 'element' | 'pix' | 'card'
  status: AttemptStatus
  status_detail: string | null
  client_data: {
    client_secret?: string
    qr_code?: string
    qr_code_base64?: string
    ticket_url?: string
    pix_code?: string
  }
  created_at: string
}

export interface Checkout {
  id: string
  status: CheckoutStatus
  provider: string
  provider_config: { publishable_key?: string; public_key?: string; methods?: string[]; max_installments?: number }
  total_cents: number
  currency: string
  expires_at: string
  paid_at: string | null
  orders: Order[]
  attempts: Attempt[]
}

export interface Provider {
  name: string
  methods: string[]
  config: Record<string, unknown>
}

export interface SalesSummary {
  gross_cents: number
  fees_cents: number
  refunded_cents: number
  paid_out_cents: number
  in_escrow_cents: number
  to_receive_cents: number
  payout_account: boolean
}

export function useCart(enabled = true) {
  return useQuery({ queryKey: ['cart'], queryFn: () => api<Cart>('/api/cart'), enabled })
}

export function useProviders() {
  return useQuery({
    queryKey: ['providers'],
    queryFn: () => api<Provider[]>('/api/payments/providers'),
    staleTime: Infinity,
  })
}
