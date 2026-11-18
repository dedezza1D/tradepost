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
}

export interface ListingCard {
  id: string
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
