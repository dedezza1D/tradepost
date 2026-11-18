export type ListingStatus = 'draft' | 'active' | 'paused' | 'sold_out' | 'removed'
export type Condition = 'new' | 'used'
export type SortOrder = 'relevance' | 'newest' | 'price_asc' | 'price_desc' | 'nearest'

export interface Me {
  id: string
  email: string
  display_name: string
  city: string | null
  state: string | null
  municipality_id: number | null
  locale: 'pt-BR' | 'en'
  is_admin: boolean
  created_at: string
  bio: string | null
  avatar_url: string | null
}

export interface Category {
  slug: string
  name: string
}

export interface Photo {
  id: string
  position: number
  url: string
  thumb_url: string
  width: number
  height: number
}

export interface Seller {
  id: string
  display_name: string
  avatar_url: string | null
  reputation: string
  rating_avg: number | null
  rating_count: number
}

export interface Listing {
  id: string
  seller: Seller
  title: string
  description: string
  category_slug: string
  condition: Condition
  price_cents: number
  currency: string
  shipping_cents: number
  stock: number
  city: string
  state: string
  municipality_id: number | null
  status: ListingStatus
  version: number
  photos: Photo[]
  created_at: string
  published_at: string | null
  save_count: number
  saved: boolean
}

export interface ListingCard {
  id: string
  seller_id?: string | null
  title: string
  price_cents: number
  currency: string
  condition: Condition
  city: string
  state: string
  status: ListingStatus
  thumb_url: string | null
  published_at: string | null
  distance_km?: number | null
  save_count?: number
  saved?: boolean
}

export interface ListingPage {
  items: ListingCard[]
  next_cursor: string | null
}

export interface ListingInput {
  title: string
  description: string
  category_slug: string
  condition: Condition
  price_cents: number
  stock: number
  shipping_cents: number
  city: string
  state: string
}

export const BR_STATES = [
  'AC', 'AL', 'AP', 'AM', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MT', 'MS', 'MG', 'PA',
  'PB', 'PR', 'PE', 'PI', 'RJ', 'RN', 'RS', 'RO', 'RR', 'SC', 'SP', 'SE', 'TO',
] as const

export type FeedTab = 'trending' | 'nearby' | 'following'

export interface Profile {
  id: string
  display_name: string
  bio: string | null
  avatar_url: string | null
  city: string | null
  state: string | null
  member_since: string
  rating_avg: number | null
  rating_count: number
  follower_count: number
  following_count: number
  listing_count: number
  is_me: boolean
  following: boolean
  blocked: boolean
  reputation: {
    level: string
    completed_sales: number
    cancel_rate: number
    late_rate: number
    rating_avg: number | null
    rating_count: number
  }
}

export interface UserSummary {
  id: string
  display_name: string
  avatar_url: string | null
  city: string | null
  state: string | null
  follower_count: number
}

export type ReportReason = 'prohibited' | 'scam' | 'offensive' | 'spam' | 'other'
export const REPORT_REASONS: ReportReason[] = ['prohibited', 'scam', 'offensive', 'spam', 'other']

export interface AdminReport {
  id: string
  target_type: 'listing' | 'user'
  target_id: string
  reason: ReportReason
  details: string | null
  status: 'open' | 'dismissed' | 'actioned'
  created_at: string
  target_label: string
  reports_on_target: number
}

export type OfferStatus = 'pending' | 'accepted' | 'declined' | 'countered' | 'cancelled' | 'expired'
export type MessageKind = 'text' | 'offer' | 'offer_accepted' | 'offer_declined' | 'offer_cancelled' | 'offer_expired'

export interface Offer {
  id: string
  proposer_id: string
  amount_cents: number
  quantity: number
  status: OfferStatus
  expires_at: string
  pay_by: string | null
  checkout_id: string | null
  paid: boolean
}

export interface ChatMessage {
  id: number
  sender_id: string | null
  kind: MessageKind
  body: string | null
  warning: 'contact' | 'off_platform_payment' | null
  created_at: string
  offer: Offer | null
}

export interface Conversation {
  id: string
  role: 'buyer' | 'seller'
  listing: { id: string; title: string; price_cents: number; status: ListingStatus; thumb_url: string | null }
  other: { id: string; display_name: string; avatar_url: string | null }
  last_message_at: string
  last_message_preview: string | null
  unread: number
  other_read_at: string | null
  blocked: boolean
}
