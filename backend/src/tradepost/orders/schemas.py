import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, EmailStr, Field, StringConstraints

from tradepost.catalog.models import ListingStatus
from tradepost.orders.models import AttemptStatus, CheckoutStatus, OrderStatus

ShippingService = Literal["standard", "express"]


class CartLineIn(BaseModel):
    quantity: int = Field(ge=0, le=10)
    variant_id: uuid.UUID | None = None


class CartLineOut(BaseModel):
    listing_id: uuid.UUID
    variant_id: uuid.UUID | None = None
    variant_name: str | None = None
    title: str
    price_cents: int
    shipping_cents: int
    quantity: int
    available: int
    status: ListingStatus
    thumb_url: str | None
    seller_id: uuid.UUID
    seller_name: str


class CartOut(BaseModel):
    items: list[CartLineOut]
    items_cents: int
    shipping_cents: int
    total_cents: int


class CheckoutLineIn(BaseModel):
    listing_id: uuid.UUID
    quantity: int = Field(ge=1, le=10)
    variant_id: uuid.UUID | None = None


class Delivery(BaseModel):
    # City to ship to (IBGE code); defaults to the buyer's city. Used by calculated shipping.
    ship_to: int | None = Field(default=None, ge=1_000_000, le=9_999_999)
    shipping_service: ShippingService = "standard"
    coupon_code: str | None = Field(default=None, max_length=20)


class CheckoutIn(Delivery):
    provider: str = Field(max_length=20)
    # Either explicit lines ("buy now") or the whole cart.
    items: list[CheckoutLineIn] | None = Field(default=None, max_length=20)
    from_cart: bool = False


class CardIn(BaseModel):
    token: str = Field(min_length=8, max_length=200)
    payment_method_id: str = Field(max_length=40)
    installments: int = Field(ge=1, le=12)
    payer_email: EmailStr
    issuer_id: str | None = Field(default=None, max_length=40)


class PaymentIn(BaseModel):
    method: Literal["element", "pix", "card"]
    card: CardIn | None = None


class AttemptOut(BaseModel):
    id: uuid.UUID
    method: str
    status: AttemptStatus
    status_detail: str | None
    client_data: dict[str, Any]
    created_at: datetime


class PartyOut(BaseModel):
    id: uuid.UUID
    display_name: str


class OrderItemOut(BaseModel):
    listing_id: uuid.UUID
    title: str
    variant_name: str | None = None
    unit_price_cents: int
    quantity: int
    shipping_cents: int
    thumb_url: str | None


class OrderEventOut(BaseModel):
    from_status: str | None
    to_status: str
    actor: str
    note: str | None
    created_at: datetime


class OrderOut(BaseModel):
    id: uuid.UUID
    checkout_id: uuid.UUID
    status: OrderStatus
    buyer: PartyOut
    seller: PartyOut
    items: list[OrderItemOut]
    items_cents: int
    shipping_cents: int
    discount_cents: int = 0
    total_cents: int
    installments: int | None = None
    dispute_id: uuid.UUID | None = None
    dispute_status: str | None = None
    platform_fee_cents: int | None = None  # seller only
    seller_net_cents: int | None = None  # seller only
    tracking_code: str | None
    shipping_service: str | None = None
    shipping_days: int | None = None
    ship_to: str | None = None
    created_at: datetime
    paid_at: datetime | None
    shipped_at: datetime | None
    delivered_at: datetime | None
    events: list[OrderEventOut] = []
    reviewed: bool = False


class CheckoutOut(BaseModel):
    id: uuid.UUID
    status: CheckoutStatus
    provider: str
    provider_config: dict[str, Any]
    total_cents: int
    currency: str
    expires_at: datetime
    paid_at: datetime | None
    orders: list[OrderOut]
    attempts: list[AttemptOut]


class ProviderOut(BaseModel):
    name: str
    methods: list[str]
    config: dict[str, Any]


TrackingCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=4, max_length=60)]


class ShipIn(BaseModel):
    tracking_code: TrackingCode


class SellerSummaryOut(BaseModel):
    gross_cents: int
    fees_cents: int
    refunded_cents: int
    paid_out_cents: int
    in_escrow_cents: int
    to_receive_cents: int
    payout_account: bool


class PayoutAccountIn(BaseModel):
    provider: str = Field(default="stripe", max_length=20)


class PayoutAccountOut(BaseModel):
    provider: str
    connected: bool
    onboarding_url: str | None = None


class QuoteIn(Delivery):
    items: list[CheckoutLineIn] | None = Field(default=None, max_length=20)
    from_cart: bool = False
    offer_id: uuid.UUID | None = None


class QuoteLineOut(BaseModel):
    listing_id: uuid.UUID
    variant_id: uuid.UUID | None
    title: str
    variant_name: str | None
    quantity: int
    unit_price_cents: int
    shipping_cents: int


class QuoteOrderOut(BaseModel):
    seller_id: uuid.UUID
    items: list[QuoteLineOut]
    items_cents: int
    shipping_cents: int
    discount_cents: int
    total_cents: int
    shipping_service: str | None
    shipping_days: int | None


class QuoteOut(BaseModel):
    orders: list[QuoteOrderOut]
    total_cents: int
    destination: str | None
    shipping_by_service: dict[str, int]
